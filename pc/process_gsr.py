"""
GSR Signal Processor
Decomposes raw GSR into SCL (tonic) and SCR (phasic) components.
Computes dual-channel synchrony metrics.

Processing pipeline (based on neurokit2):
  1. Raw ADC → Conductance (μS) via Grove GSR formula
  2. eda_clean(): Butterworth LPF 3Hz, 4th order (noise removal)
  3. eda_phasic(): Tonic/Phasic decomposition (highpass or cvxEDA)
  4. eda_peaks(): SCR peak detection with Gaussian smoothing
  5. Dual-channel synchrony analysis

Usage:
    python process_gsr.py data/session_20260302_120000.csv
    python process_gsr.py data/session_20260302_120000.csv --plot
    python process_gsr.py data/session_20260302_120000.csv --sync
    python process_gsr.py data/session_20260302_120000.csv --method cvxEDA --plot
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal as sig
from scipy.ndimage import gaussian_filter1d

# Try to import neurokit2 for standard EDA pipeline
try:
    import neurokit2 as nk

    HAS_NK = True
except ImportError:
    HAS_NK = False
    print("Warning: neurokit2 not installed. Using fallback scipy pipeline.")
    print("  Install with: pip install neurokit2")


# ── Grove GSR Sensor Constants ─────────────────────────────────────
# Reference: https://wiki.seeedstudio.com/Grove-GSR_Sensor/
#
# Official formula (10-bit ADC, 0-1023):
#   R_human = ((1024 + 2 * ADC) * 10000) / (512 - ADC)
#
# For 12-bit ADC (XIAO ESP32C3, 0-4095), we scale:
#   R_human = ((4096 + 2 * ADC) * 10000) / (2048 - ADC)
#
# Note: The denominator uses (ADC_MAX/2 - ADC). The Seeedstudio wiki
# uses a calibration value from adjusting the potentiometer, but for
# a demo setup without calibration, ADC_MAX/2 is a reasonable default.
# If you calibrate, replace SERIAL_CALIBRATION below.
ADC_BITS = 12
ADC_MAX = 2**ADC_BITS  # 4096
SERIAL_CALIBRATION = ADC_MAX // 2  # 2048 (default; adjust after calibration)
R_SCALE = 10_000  # 10kΩ in the Grove GSR circuit


def raw_to_conductance(raw: np.ndarray) -> np.ndarray:
    """
    Convert raw ADC values to skin conductance (μS).

    Uses the official Grove GSR formula adapted for 12-bit ADC:
        R_human (Ω) = ((4096 + 2 * ADC) * 10000) / (2048 - ADC)
        Conductance (μS) = 1e6 / R_human

    Values at or above the calibration point are clamped to avoid
    division by zero or negative resistance.
    """
    raw_clamped = np.clip(raw, 0, SERIAL_CALIBRATION - 1)

    resistance = ((ADC_MAX + 2.0 * raw_clamped) * R_SCALE) / (
        SERIAL_CALIBRATION - raw_clamped
    )
    # Avoid zero resistance
    resistance = np.maximum(resistance, 1.0)
    conductance_us = 1e6 / resistance
    return conductance_us


# ── NeuroKit2-based Pipeline ──────────────────────────────────────


def process_channel_nk(
    conductance: np.ndarray,
    fs: float = 100.0,
    method: str = "highpass",
    gaussian_sigma: float | None = None,
) -> dict:
    """
    Process a single GSR channel using neurokit2.

    Pipeline:
      1. eda_clean() — Butterworth LPF 3Hz (removes high-freq noise)
      2. eda_phasic() — Decomposes into Tonic (SCL) and Phasic (SCR)
         - "highpass": Butterworth HPF 0.05Hz (Biopac standard)
         - "cvxEDA":  Convex optimization (Greco 2016, requires cvxopt)
         - "smoothmedian": Median smoothing (Biopac Acqknowledge)
      3. Optional Gaussian smoothing on phasic component
      4. eda_peaks() — SCR peak detection

    Args:
        conductance: Skin conductance in μS
        fs: Sampling rate in Hz
        method: Decomposition method ("highpass", "cvxEDA", "smoothmedian")
        gaussian_sigma: If set, apply Gaussian smoothing (σ in samples)
                        to the phasic component before peak detection.
                        Typical: 0.2*fs (0.2s window at 100Hz → σ=20)
    """
    # Step 1: Clean (LPF 3Hz, Butterworth 4th order)
    cleaned = nk.eda_clean(conductance, sampling_rate=int(fs), method="neurokit")

    # Step 2: Tonic/Phasic decomposition
    decomposed = nk.eda_phasic(cleaned, sampling_rate=int(fs), method=method)
    scl = decomposed["EDA_Tonic"].values
    scr = decomposed["EDA_Phasic"].values

    # Step 3: Optional Gaussian smoothing on phasic
    scr_smoothed = scr
    if gaussian_sigma is not None and gaussian_sigma > 0:
        scr_smoothed = gaussian_filter1d(scr, sigma=gaussian_sigma)

    # Step 4: Peak detection via neurokit2
    peak_signal, peak_info = nk.eda_peaks(
        scr_smoothed, sampling_rate=int(fs), method="neurokit"
    )
    peak_indices = np.where(peak_signal["SCR_Peaks"].values == 1)[0]
    peak_amplitudes = (
        peak_info.get("SCR_Amplitude", pd.Series(dtype=float)).values
        if "SCR_Amplitude" in peak_info
        else scr_smoothed[peak_indices] if len(peak_indices) > 0 else np.array([])
    )

    duration_sec = len(conductance) / fs
    return {
        "cleaned": cleaned,
        "scl": scl,
        "scr": scr,
        "scr_smoothed": scr_smoothed,
        "peaks": {
            "indices": peak_indices,
            "amplitudes": peak_amplitudes,
            "times_sec": peak_indices / fs,
            "count": len(peak_indices),
            "rate_per_min": len(peak_indices) / (duration_sec / 60)
            if duration_sec > 0
            else 0,
        },
    }


# ── Fallback scipy-only Pipeline ─────────────────────────────────


def process_channel_scipy(
    conductance: np.ndarray,
    fs: float = 100.0,
    gaussian_sigma: float | None = None,
) -> dict:
    """
    Fallback pipeline using only scipy (when neurokit2 is unavailable).

    Pipeline:
      1. Butterworth LPF 3Hz (noise removal, matches neurokit2 default)
      2. Butterworth HPF 0.05Hz (tonic/phasic split, Biopac standard)
      3. Optional Gaussian smoothing on phasic
      4. scipy peak detection
    """
    nyq = fs / 2.0

    # Step 1: Clean — LPF 3Hz
    clean_cutoff = min(3.0, nyq * 0.9)
    b_lp, a_lp = sig.butter(4, clean_cutoff / nyq, btype="low")
    cleaned = sig.filtfilt(b_lp, a_lp, conductance)

    # Step 2: Tonic/Phasic — HPF 0.05Hz
    phasic_cutoff = min(0.05, nyq * 0.9)
    b_hp, a_hp = sig.butter(4, phasic_cutoff / nyq, btype="high")
    scr = sig.filtfilt(b_hp, a_hp, cleaned)
    scl = cleaned - scr

    # Step 3: Optional Gaussian smoothing
    scr_smoothed = scr
    if gaussian_sigma is not None and gaussian_sigma > 0:
        scr_smoothed = gaussian_filter1d(scr, sigma=gaussian_sigma)

    # Step 4: Peak detection
    min_distance_samples = int(1.0 * fs)
    peaks_idx, props = sig.find_peaks(
        scr_smoothed,
        height=0.01,
        distance=min_distance_samples,
        prominence=0.005,
    )

    duration_sec = len(conductance) / fs
    return {
        "cleaned": cleaned,
        "scl": scl,
        "scr": scr,
        "scr_smoothed": scr_smoothed,
        "peaks": {
            "indices": peaks_idx,
            "amplitudes": scr_smoothed[peaks_idx]
            if len(peaks_idx) > 0
            else np.array([]),
            "times_sec": peaks_idx / fs,
            "count": len(peaks_idx),
            "rate_per_min": len(peaks_idx) / (duration_sec / 60)
            if duration_sec > 0
            else 0,
        },
    }


# ── Synchrony Analysis ───────────────────────────────────────────


def compute_synchrony(
    peaks1: dict,
    peaks2: dict,
    tolerance_sec: float = 2.0,
) -> dict:
    """
    Compute dual-channel SCR synchrony.

    For each peak in ch1, find the closest peak in ch2 within tolerance.
    Reports synchrony ratio and mean latency.
    """
    t1 = peaks1["times_sec"]
    t2 = peaks2["times_sec"]

    if len(t1) == 0 or len(t2) == 0:
        return {
            "matched_count": 0,
            "total_ch1": len(t1),
            "total_ch2": len(t2),
            "synchrony_ratio": 0.0,
            "mean_latency_sec": float("nan"),
            "latencies": np.array([]),
        }

    matched = 0
    latencies = []

    for t in t1:
        diffs = np.abs(t2 - t)
        min_idx = np.argmin(diffs)
        if diffs[min_idx] <= tolerance_sec:
            matched += 1
            latencies.append(t2[min_idx] - t)

    total = max(len(t1), len(t2))
    return {
        "matched_count": matched,
        "total_ch1": len(t1),
        "total_ch2": len(t2),
        "synchrony_ratio": matched / total if total > 0 else 0.0,
        "mean_latency_sec": np.mean(latencies) if latencies else float("nan"),
        "latencies": np.array(latencies),
    }


# ── Main Processing ──────────────────────────────────────────────


def process_file(
    filepath: Path,
    fs: float = 100.0,
    method: str = "highpass",
    gaussian_sigma: float | None = None,
) -> dict:
    """Process a recorded GSR CSV file."""
    df = pd.read_csv(filepath)

    expected = ["timestamp_ms", "gsr1_raw", "gsr2_raw"]
    if not all(c in df.columns for c in expected):
        print(f"Error: Expected columns {expected}, got {list(df.columns)}")
        sys.exit(1)

    print(f"Loaded {len(df)} samples from {filepath.name}")
    duration = (df["timestamp_ms"].iloc[-1] - df["timestamp_ms"].iloc[0]) / 1000
    actual_rate = len(df) / duration if duration > 0 else 0
    print(f"Duration: {duration:.1f}s  ({actual_rate:.1f} Hz actual)")

    if gaussian_sigma is not None:
        print(f"Gaussian smoothing: σ = {gaussian_sigma:.1f} samples "
              f"({gaussian_sigma/fs*1000:.0f} ms)")

    pipeline = "neurokit2" if HAS_NK else "scipy (fallback)"
    print(f"Pipeline: {pipeline}  |  Method: {method}")

    results = {}

    for ch_name, col in [("ch1", "gsr1_raw"), ("ch2", "gsr2_raw")]:
        raw = df[col].values.astype(float)
        conductance = raw_to_conductance(raw)

        if HAS_NK:
            ch_result = process_channel_nk(conductance, fs, method, gaussian_sigma)
        else:
            ch_result = process_channel_scipy(conductance, fs, gaussian_sigma)

        ch_result["raw"] = raw
        ch_result["conductance"] = conductance
        results[ch_name] = ch_result

        peaks = ch_result["peaks"]
        print(f"\n{'='*40}")
        print(f"  {ch_name.upper()}")
        print(f"{'='*40}")
        print(f"  Conductance: {conductance.mean():.2f} ± {conductance.std():.2f} μS")
        print(f"  SCL range:   {ch_result['scl'].min():.2f} – {ch_result['scl'].max():.2f} μS")
        print(f"  SCR peaks:   {peaks['count']}  ({peaks['rate_per_min']:.1f}/min)")
        if peaks["count"] > 0:
            print(f"  SCR amp:     {np.mean(peaks['amplitudes']):.3f} ± "
                  f"{np.std(peaks['amplitudes']):.3f} μS")

    results["time_sec"] = (df["timestamp_ms"].values - df["timestamp_ms"].iloc[0]) / 1000
    results["df"] = df

    return results


def plot_results(results: dict, show_gaussian: bool = False, output: Path | None = None):
    """Plot SCL/SCR decomposition for both channels."""
    t = results["time_sec"]
    plt.style.use("dark_background")

    fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    fig.suptitle("GSR Analysis – SCL/SCR Decomposition", fontsize=14, fontweight="bold")

    colors = {"ch1": "#00d4ff", "ch2": "#ff6b9d"}

    # Row 0: Raw conductance + cleaned
    for ch in ["ch1", "ch2"]:
        axes[0].plot(t, results[ch]["conductance"], color=colors[ch],
                     linewidth=0.5, alpha=0.4, label=f"{ch.upper()} raw")
        axes[0].plot(t, results[ch]["cleaned"], color=colors[ch],
                     linewidth=1.0, alpha=0.9, label=f"{ch.upper()} cleaned")
    axes[0].set_ylabel("Conductance (μS)")
    axes[0].set_title("Skin Conductance (raw vs cleaned)")
    axes[0].legend(fontsize=8)
    axes[0].grid(True, alpha=0.2)

    # Row 1: SCL (tonic)
    for ch in ["ch1", "ch2"]:
        axes[1].plot(t, results[ch]["scl"], color=colors[ch], linewidth=1.2, label=ch.upper())
    axes[1].set_ylabel("SCL (μS)")
    axes[1].set_title("Skin Conductance Level (Tonic)")
    axes[1].legend(fontsize=8)
    axes[1].grid(True, alpha=0.2)

    # Row 2-3: SCR (phasic) per channel with peaks
    for i, ch in enumerate(["ch1", "ch2"]):
        ax = axes[2 + i]
        ax.plot(t, results[ch]["scr"], color=colors[ch], linewidth=0.6,
                alpha=0.5, label=f"{ch.upper()} SCR")

        # Show Gaussian-smoothed if different
        if show_gaussian and not np.array_equal(results[ch]["scr"], results[ch]["scr_smoothed"]):
            ax.plot(t, results[ch]["scr_smoothed"], color=colors[ch],
                    linewidth=1.2, label=f"{ch.upper()} smoothed")

        peaks = results[ch]["peaks"]
        if peaks["count"] > 0:
            ax.plot(
                peaks["times_sec"],
                results[ch]["scr_smoothed"][peaks["indices"]]
                if show_gaussian
                else results[ch]["scr"][peaks["indices"]],
                "v", color="#ffdd57", markersize=6,
                label=f"Peaks (n={peaks['count']})"
            )
        ax.set_ylabel("SCR (μS)")
        ax.set_title(f"Skin Conductance Response – {ch.upper()}")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.2)

    axes[-1].set_xlabel("Time (s)")
    plt.tight_layout()

    if output:
        fig.savefig(output, dpi=150, bbox_inches="tight")
        print(f"\nPlot saved to: {output}")
    else:
        plt.show()


def print_synchrony(results: dict):
    """Compute and print synchrony analysis."""
    sync = compute_synchrony(results["ch1"]["peaks"], results["ch2"]["peaks"])

    print(f"\n{'='*40}")
    print("  SYNCHRONY ANALYSIS")
    print(f"{'='*40}")
    print(f"  CH1 peaks: {sync['total_ch1']}")
    print(f"  CH2 peaks: {sync['total_ch2']}")
    print(f"  Matched:   {sync['matched_count']}")
    print(f"  Synchrony: {sync['synchrony_ratio']:.1%}")
    if not np.isnan(sync["mean_latency_sec"]):
        print(f"  Mean lag:  {sync['mean_latency_sec']*1000:.0f} ms "
              f"(CH2 relative to CH1)")


def main():
    parser = argparse.ArgumentParser(
        description="GSR Signal Processor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Decomposition methods:
  highpass      Butterworth HPF 0.05Hz (Biopac standard, fast)
  smoothmedian  Median smoothing (Biopac Acqknowledge)
  cvxEDA        Convex optimization (Greco 2016, requires cvxopt)

Examples:
  python process_gsr.py data/session.csv --plot
  python process_gsr.py data/session.csv --method cvxEDA --plot
  python process_gsr.py data/session.csv --gaussian 20 --plot --sync
        """,
    )
    parser.add_argument("input", help="Input CSV file from receiver.py")
    parser.add_argument("--plot", action="store_true", help="Show SCL/SCR plots")
    parser.add_argument("--save-plot", help="Save plot to file instead of showing")
    parser.add_argument("--sync", action="store_true", help="Compute dual-channel synchrony")
    parser.add_argument("--fs", type=float, default=100.0, help="Sampling rate in Hz")
    parser.add_argument(
        "--method", "-m", default="highpass",
        choices=["highpass", "smoothmedian", "cvxEDA"],
        help="SCL/SCR decomposition method (default: highpass)",
    )
    parser.add_argument(
        "--gaussian", "-g", type=float, default=None,
        help="Gaussian smoothing σ in samples for phasic component. "
             "Typical: 10-30 at 100Hz (100-300ms window). "
             "Reduces noise in SCR peak detection.",
    )
    parser.add_argument(
        "--calibration", type=int, default=None,
        help="Grove GSR calibration value (raw ADC at no-skin baseline). "
             "Default: ADC_MAX/2 = 2048 for 12-bit.",
    )
    args = parser.parse_args()

    filepath = Path(args.input)
    if not filepath.exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    # Override calibration if provided
    global SERIAL_CALIBRATION
    if args.calibration is not None:
        SERIAL_CALIBRATION = args.calibration
        print(f"Using calibration value: {SERIAL_CALIBRATION}")

    results = process_file(filepath, fs=args.fs, method=args.method,
                           gaussian_sigma=args.gaussian)

    if args.sync:
        print_synchrony(results)

    if args.plot or args.save_plot:
        plot_results(
            results,
            show_gaussian=args.gaussian is not None,
            output=Path(args.save_plot) if args.save_plot else None,
        )


if __name__ == "__main__":
    main()
