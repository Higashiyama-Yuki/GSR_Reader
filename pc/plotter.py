"""
GSR Real-Time Plotter with Synchrony Analysis
Reads serial data from XIAO SAMD21 and plots live dual-channel GSR
with real-time synchrony metrics (correlation, PLV, common mode).

Usage:
    python plotter.py --port COM12
    python plotter.py --port COM12 --save --process
    python plotter.py --port COM12 --window 30
"""

import argparse
import csv
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np
import serial
import serial.tools.list_ports

from dsp import RealtimeDSP


class GSRPlotter:
    """Real-time dual-channel GSR plotter with synchrony analysis."""

    def __init__(self, port: str, baud: int, window_sec: float = 10.0,
                 save_path: Path | None = None):
        self.port = port
        self.baud = baud
        self.window_sec = window_sec
        self.sample_rate = 100

        max_points = int(window_sec * self.sample_rate * 1.2)
        self.times = deque(maxlen=max_points)
        self.gsr1 = deque(maxlen=max_points)
        self.gsr2 = deque(maxlen=max_points)
        self.common_mode = deque(maxlen=max_points)
        self.r_values = deque(maxlen=max_points)
        self.plv_values = deque(maxlen=max_points)
        self.t0 = None
        self.ser = None
        self.parse_errors = 0
        self.good_samples = 0

        # DSP engine
        self.dsp = RealtimeDSP(fs=self.sample_rate, window_sec=window_sec)
        self.latest_metrics = None

        # Recording
        self.save_path = save_path
        self.csv_file = None
        self.csv_writer = None

    def connect(self):
        """Open serial connection."""
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            print(f"Connected to {self.port}")
        except serial.SerialException as e:
            print(f"Error: {e}")
            sys.exit(1)

    def start_recording(self):
        """Open CSV file for recording."""
        if self.save_path is None:
            return
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_file = open(self.save_path, "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(["timestamp_ms", "gsr1_raw", "gsr2_raw"])
        print(f"Recording to: {self.save_path}")

    def stop_recording(self):
        """Close CSV file."""
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None
            print(f"Saved {self.good_samples} samples to: {self.save_path}")

    def read_samples(self):
        """Read all available samples from serial buffer."""
        while self.ser and self.ser.in_waiting:
            try:
                raw = self.ser.readline()
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            if not line or line.startswith("#"):
                if line and "START" in line:
                    print("(Re)detected START marker")
                continue

            parts = line.split(",")
            if len(parts) != 3:
                self.parse_errors += 1
                if self.parse_errors <= 5:
                    print(f"  [SKIP] unexpected format ({len(parts)} fields): {line!r}")
                continue

            try:
                ts_ms = int(parts[0])
                g1 = int(parts[1])
                g2 = int(parts[2])
            except ValueError:
                self.parse_errors += 1
                if self.parse_errors <= 5:
                    print(f"  [SKIP] parse error: {line!r}")
                continue

            if self.t0 is None:
                self.t0 = ts_ms

            t_sec = (ts_ms - self.t0) / 1000.0
            self.times.append(t_sec)
            self.gsr1.append(g1)
            self.gsr2.append(g2)
            self.good_samples += 1

            # DSP processing
            metrics = self.dsp.update(float(g1), float(g2))
            self.latest_metrics = metrics
            self.common_mode.append(metrics["common_mode"])
            self.r_values.append(metrics["r_value"])
            self.plv_values.append(metrics["plv"])

            # Write to CSV
            if self.csv_writer:
                self.csv_writer.writerow([ts_ms, g1, g2])

    def setup_plot(self):
        """Create the matplotlib figure with synchrony panels."""
        plt.style.use("dark_background")
        self.fig, axes = plt.subplots(
            4, 1, figsize=(12, 9),
            gridspec_kw={"height_ratios": [3, 3, 2, 2]},
        )
        self.ax_ch1, self.ax_ch2, self.ax_common, self.ax_sync = axes

        title = "GSR Dual Sensor — Live Synchrony Analysis"
        if self.save_path:
            title += "  ● REC"
        self.fig.suptitle(title, fontsize=13, fontweight="bold")

        # CH1
        (self.line1,) = self.ax_ch1.plot(
            [], [], color="#00d4ff", linewidth=1.0, label="CH1 (A0)"
        )
        self.ax_ch1.set_ylabel("ADC")
        self.ax_ch1.legend(loc="upper right", fontsize=8)
        self.ax_ch1.grid(True, alpha=0.2)

        # CH2
        (self.line2,) = self.ax_ch2.plot(
            [], [], color="#ff6b9d", linewidth=1.0, label="CH2 (A1)"
        )
        self.ax_ch2.set_ylabel("ADC")
        self.ax_ch2.legend(loc="upper right", fontsize=8)
        self.ax_ch2.grid(True, alpha=0.2)

        # Common mode
        (self.line_common,) = self.ax_common.plot(
            [], [], color="#a0e060", linewidth=1.2, label="共通モード (CH1+CH2)/2"
        )
        self.ax_common.set_ylabel("ADC")
        self.ax_common.legend(loc="upper right", fontsize=8)
        self.ax_common.grid(True, alpha=0.2)

        # Synchrony (r + PLV)
        (self.line_r,) = self.ax_sync.plot(
            [], [], color="#ffdd57", linewidth=1.2, label="Pearson r"
        )
        (self.line_plv,) = self.ax_sync.plot(
            [], [], color="#ff8c42", linewidth=1.2, label="PLV"
        )
        self.ax_sync.set_ylabel("同期度")
        self.ax_sync.set_xlabel("Time (s)")
        self.ax_sync.set_ylim(-0.1, 1.1)
        self.ax_sync.axhline(y=0, color="#555555", linewidth=0.5)
        self.ax_sync.axhline(y=1, color="#555555", linewidth=0.5)
        self.ax_sync.legend(loc="upper right", fontsize=8)
        self.ax_sync.grid(True, alpha=0.2)

        # Stats text
        self.stats_text = self.fig.text(
            0.01, 0.01, "", fontsize=9, color="#aaaaaa",
            fontfamily="monospace", verticalalignment="bottom",
        )

        plt.tight_layout(rect=[0, 0.03, 1, 0.97])

    def update(self, frame):
        """Animation update callback."""
        self.read_samples()

        if not self.times:
            return (self.line1, self.line2, self.line_common,
                    self.line_r, self.line_plv, self.stats_text)

        t = list(self.times)
        g1 = list(self.gsr1)
        g2 = list(self.gsr2)
        cm = list(self.common_mode)
        rv = list(self.r_values)
        pv = list(self.plv_values)

        # Update lines
        self.line1.set_data(t, g1)
        self.line2.set_data(t, g2)
        self.line_common.set_data(t, cm)

        # r and PLV (filter NaN for display)
        t_arr = np.array(t)
        rv_arr = np.array(rv)
        pv_arr = np.array(pv)
        valid_r = ~np.isnan(rv_arr)
        valid_p = ~np.isnan(pv_arr)
        self.line_r.set_data(t_arr[valid_r], rv_arr[valid_r])
        self.line_plv.set_data(t_arr[valid_p], pv_arr[valid_p])

        # Sliding window X limits
        t_max = t[-1]
        t_min = max(0, t_max - self.window_sec)
        for ax in [self.ax_ch1, self.ax_ch2, self.ax_common, self.ax_sync]:
            ax.set_xlim(t_min, t_max + 0.5)

        # Auto-scale Y for signal panels
        vis = [i for i, tv in enumerate(t) if tv >= t_min]
        if vis:
            margin = 50
            for ax, data in [(self.ax_ch1, g1), (self.ax_ch2, g2), (self.ax_common, cm)]:
                vis_d = [data[i] for i in vis]
                y_lo, y_hi = min(vis_d) - margin, max(vis_d) + margin
                ax.set_ylim(max(0, y_lo), min(4095, y_hi))

        # Stats
        m = self.latest_metrics
        if m:
            r_str = f"{m['r_value']:.2f}" if not np.isnan(m["r_value"]) else "---"
            plv_str = f"{m['plv']:.2f}" if not np.isnan(m["plv"]) else "---"
            rec = "● REC  " if self.save_path else ""
            self.stats_text.set_text(
                f"{rec}N={self.good_samples}  |  "
                f"CH1={g1[-1]:4d}  CH2={g2[-1]:4d}  |  "
                f"r={r_str}  PLV={plv_str}"
            )

        return (self.line1, self.line2, self.line_common,
                self.line_r, self.line_plv, self.stats_text)

    def run(self, auto_process: bool = False):
        """Start the live plot."""
        self.connect()

        print("Waiting for data stream...")
        timeout = time.time() + 10
        started = False
        while time.time() < timeout:
            try:
                raw = self.ser.readline()
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if line:
                print(f"  Received: {line!r}")
            if "START" in line:
                print("Stream detected!")
                started = True
                break
        if not started:
            print("Warning: No START marker detected, proceeding anyway...")

        self.start_recording()
        self.setup_plot()

        self.ani = animation.FuncAnimation(
            self.fig,
            self.update,
            interval=50,
            blit=False,
            cache_frame_data=False,
        )

        print("Plotting... Close window or Ctrl+C to stop.")
        try:
            plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            if self.ser:
                self.ser.close()
            self.stop_recording()
            print(f"Done. Samples: {self.good_samples}, Errors: {self.parse_errors}")

        if auto_process and self.save_path and self.good_samples > 0:
            self._run_processing()

    def _run_processing(self):
        """Launch process_gsr.py on saved data."""
        script = Path(__file__).parent / "process_gsr.py"
        cmd = [sys.executable, str(script), str(self.save_path), "--plot", "--sync"]
        print(f"\n{'='*50}")
        print(f"  分析開始: process_gsr.py")
        print(f"  ファイル: {self.save_path}")
        print(f"{'='*50}\n")
        subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(description="GSR Real-Time Plotter")
    parser.add_argument("--port", "-p", help="Serial port (e.g. COM12)")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate")
    parser.add_argument(
        "--window", "-w", type=float, default=10.0,
        help="Display window in seconds (default: 10)",
    )
    parser.add_argument("--save", "-s", action="store_true", help="Save data to CSV")
    parser.add_argument("--output", "-o", help="Output CSV path (with --save)")
    parser.add_argument(
        "--process", action="store_true",
        help="Auto-run analysis after recording (implies --save)",
    )
    parser.add_argument("--list", "-l", action="store_true", help="List serial ports")
    args = parser.parse_args()

    if args.list:
        ports = serial.tools.list_ports.comports()
        if not ports:
            print("No serial ports found.")
        for p in ports:
            print(f"  {p.device} - {p.description}")
        return

    if not args.port:
        ports = serial.tools.list_ports.comports()
        if not ports:
            print("No serial ports found.")
        else:
            print("Available serial ports:")
            for p in ports:
                print(f"  {p.device} - {p.description}")
        print("\nSpecify a port with --port")
        sys.exit(1)

    if args.process:
        args.save = True

    save_path = None
    if args.save:
        if args.output:
            save_path = Path(args.output)
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = Path(f"data/session_{ts}.csv")

    plotter = GSRPlotter(args.port, args.baud, args.window, save_path)
    plotter.run(auto_process=args.process)


if __name__ == "__main__":
    main()
