"""
Real-time DSP module for dual-channel GSR synchrony analysis.

All functions are designed for causal (real-time) processing:
  - No future samples are used
  - State is maintained between calls via class attributes
  - NumPy SIMD is leveraged internally (no manual SIMD needed at 100Hz)

Synchrony metrics:
  1. Common Mode:     (CH1 + CH2) / 2  — shared signal
  2. Differential:    (CH1 - CH2) / 2  — noise / artifact
  3. Rolling Pearson: sliding-window correlation coefficient
  4. PLV:             Phase Locking Value via Hilbert transform
"""

import numpy as np
from scipy import signal as sig


class RealtimeDSP:
    """Real-time dual-channel GSR synchrony processor.

    Designed for 100 Hz, 2-channel GSR data. All methods are causal.

    Usage:
        dsp = RealtimeDSP(fs=100)
        # Feed samples one-at-a-time or in batches:
        metrics = dsp.update(gsr1_value, gsr2_value)
        # metrics contains: common_mode, differential, r_value, plv, phase_diff
    """

    def __init__(self, fs: float = 100.0, window_sec: float = 10.0):
        self.fs = fs
        self.window_n = int(window_sec * fs)

        # Ring buffers for raw (EMA-smoothed) data
        self.buf1 = np.zeros(self.window_n)
        self.buf2 = np.zeros(self.window_n)
        self.idx = 0          # total samples received
        self.buf_len = 0      # valid samples in buffer (up to window_n)

        # ── EMA smoother (causal Gaussian approximation) ──
        # α ≈ 2/(N+1), N=20 → ~200ms window at 100Hz
        self.ema_alpha = 2.0 / (20 + 1)
        self.ema1 = 0.0
        self.ema2 = 0.0
        self.ema_initialized = False

        # ── Causal bandpass for SCR band (0.05–0.5 Hz) ──
        # Used before Hilbert transform for PLV
        nyq = fs / 2.0
        low = 0.05 / nyq
        high = min(0.5 / nyq, 0.99)
        self.sos_bp = sig.butter(4, [low, high], btype="band", output="sos")
        # Filter state (zi) for causal filtering
        self.zi1 = sig.sosfilt_zi(self.sos_bp) * 0
        self.zi2 = sig.sosfilt_zi(self.sos_bp) * 0

        # Bandpassed ring buffers
        self.bp1 = np.zeros(self.window_n)
        self.bp2 = np.zeros(self.window_n)

        # ── PLV parameters ──
        # Minimum samples needed for meaningful PLV
        # At least 2 full cycles of lowest freq (0.05 Hz) = 40s
        # But we use 10s window for responsiveness (tradeoff)
        self.plv_min_samples = int(5.0 * fs)  # 5秒でPLV計算開始

    def update(self, g1: float, g2: float) -> dict:
        """Process one sample pair and return synchrony metrics.

        Args:
            g1: GSR channel 1 raw ADC value
            g2: GSR channel 2 raw ADC value

        Returns:
            dict with keys:
                - ema1, ema2: EMA-smoothed values
                - common_mode: (ema1 + ema2) / 2
                - differential: (ema1 - ema2) / 2
                - r_value: rolling Pearson correlation (-1 to 1), or NaN
                - plv: Phase Locking Value (0 to 1), or NaN
                - phase_diff: mean phase difference (radians), or NaN
                - n_samples: total samples processed
        """
        # ── EMA smoothing ──
        if not self.ema_initialized:
            self.ema1 = g1
            self.ema2 = g2
            self.ema_initialized = True
        else:
            self.ema1 += self.ema_alpha * (g1 - self.ema1)
            self.ema2 += self.ema_alpha * (g2 - self.ema2)

        # ── Store in ring buffer ──
        pos = self.idx % self.window_n
        self.buf1[pos] = self.ema1
        self.buf2[pos] = self.ema2

        # ── Causal bandpass (sample-by-sample) ──
        bp1_sample, self.zi1 = sig.sosfilt(
            self.sos_bp, np.array([self.ema1]), zi=self.zi1
        )
        bp2_sample, self.zi2 = sig.sosfilt(
            self.sos_bp, np.array([self.ema2]), zi=self.zi2
        )
        self.bp1[pos] = bp1_sample[0]
        self.bp2[pos] = bp2_sample[0]

        self.idx += 1
        self.buf_len = min(self.idx, self.window_n)

        # ── Compute metrics ──
        common = (self.ema1 + self.ema2) / 2.0
        diff = (self.ema1 - self.ema2) / 2.0

        r_value = self._rolling_pearson()
        plv, phase_diff = self._compute_plv()

        return {
            "ema1": self.ema1,
            "ema2": self.ema2,
            "common_mode": common,
            "differential": diff,
            "r_value": r_value,
            "plv": plv,
            "phase_diff": phase_diff,
            "n_samples": self.idx,
        }

    def update_batch(self, g1_arr: np.ndarray, g2_arr: np.ndarray) -> dict:
        """Process a batch of samples. Returns metrics for the last sample."""
        result = None
        for g1, g2 in zip(g1_arr, g2_arr):
            result = self.update(float(g1), float(g2))
        return result

    def _get_valid_slice(self, buf: np.ndarray) -> np.ndarray:
        """Get the valid portion of a ring buffer in chronological order."""
        if self.buf_len < self.window_n:
            return buf[:self.buf_len]
        else:
            pos = self.idx % self.window_n
            return np.roll(buf, -pos)

    def _rolling_pearson(self) -> float:
        """Compute Pearson correlation over the current window."""
        if self.buf_len < 50:  # Need at least 0.5s
            return float("nan")

        s1 = self._get_valid_slice(self.buf1)
        s2 = self._get_valid_slice(self.buf2)

        std1 = np.std(s1)
        std2 = np.std(s2)
        if std1 < 1e-10 or std2 < 1e-10:
            return float("nan")

        r = np.corrcoef(s1, s2)[0, 1]
        return float(r)

    def _compute_plv(self) -> tuple[float, float]:
        """Compute Phase Locking Value from bandpassed signals.

        PLV = |mean(exp(j * (phase1 - phase2)))|

        Returns (plv, mean_phase_diff) or (NaN, NaN) if insufficient data.
        """
        if self.buf_len < self.plv_min_samples:
            return float("nan"), float("nan")

        s1 = self._get_valid_slice(self.bp1)
        s2 = self._get_valid_slice(self.bp2)

        # Check for near-zero signal (no SCR activity)
        if np.std(s1) < 1e-10 or np.std(s2) < 1e-10:
            return float("nan"), float("nan")

        # Hilbert transform → analytic signal → instantaneous phase
        analytic1 = sig.hilbert(s1)
        analytic2 = sig.hilbert(s2)

        phase1 = np.angle(analytic1)
        phase2 = np.angle(analytic2)

        # Phase difference
        phase_diff = phase1 - phase2

        # PLV = magnitude of mean unit vector
        plv = float(np.abs(np.mean(np.exp(1j * phase_diff))))
        mean_diff = float(np.angle(np.mean(np.exp(1j * phase_diff))))

        return plv, mean_diff
