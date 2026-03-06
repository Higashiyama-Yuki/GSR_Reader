"""
GSR Real-Time Plotter
Reads serial data from XIAO SAMD21 and plots live dual-channel GSR.

Usage:
    python plotter.py --port COM5
    python plotter.py --port COM5 --window 30   # 30-second window
"""

import argparse
import sys
import time
from collections import deque

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import serial
import serial.tools.list_ports


class GSRPlotter:
    """Real-time dual-channel GSR plotter."""

    def __init__(self, port: str, baud: int, window_sec: float = 10.0):
        self.port = port
        self.baud = baud
        self.window_sec = window_sec
        self.sample_rate = 100  # Expected Hz

        max_points = int(window_sec * self.sample_rate * 1.2)  # 20% buffer
        self.times = deque(maxlen=max_points)
        self.gsr1 = deque(maxlen=max_points)
        self.gsr2 = deque(maxlen=max_points)
        self.t0 = None
        self.ser = None
        self.parse_errors = 0
        self.good_samples = 0

    def connect(self):
        """Open serial connection."""
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            print(f"Connected to {self.port}")
        except serial.SerialException as e:
            print(f"Error: {e}")
            sys.exit(1)

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

    def setup_plot(self):
        """Create the matplotlib figure."""
        plt.style.use("dark_background")
        self.fig, (self.ax1, self.ax2) = plt.subplots(
            2, 1, figsize=(12, 6), sharex=True
        )
        self.fig.suptitle("GSR Dual Sensor - Live", fontsize=14, fontweight="bold")

        # Channel 1 (Grove A0)
        (self.line1,) = self.ax1.plot([], [], color="#00d4ff", linewidth=1.2, label="GSR 1 (Grove A0)")
        self.ax1.set_ylabel("Raw ADC (12-bit)")
        self.ax1.set_ylim(0, 4095)
        self.ax1.legend(loc="upper right")
        self.ax1.grid(True, alpha=0.3)

        # Channel 2 (Pin A1)
        (self.line2,) = self.ax2.plot([], [], color="#ff6b9d", linewidth=1.2, label="GSR 2 (Pin A1)")
        self.ax2.set_ylabel("Raw ADC (12-bit)")
        self.ax2.set_xlabel("Time (s)")
        self.ax2.set_ylim(0, 4095)
        self.ax2.legend(loc="upper right")
        self.ax2.grid(True, alpha=0.3)

        # Stats text
        self.stats_text = self.ax1.text(
            0.01, 0.95, "", transform=self.ax1.transAxes,
            fontsize=9, verticalalignment="top", color="#aaaaaa",
            fontfamily="monospace",
        )

        plt.tight_layout()

    def update(self, frame):
        """Animation update callback."""
        self.read_samples()

        if not self.times:
            return self.line1, self.line2, self.stats_text

        times_list = list(self.times)
        gsr1_list = list(self.gsr1)
        gsr2_list = list(self.gsr2)

        self.line1.set_data(times_list, gsr1_list)
        self.line2.set_data(times_list, gsr2_list)

        # Sliding window
        t_max = times_list[-1]
        t_min = max(0, t_max - self.window_sec)
        self.ax1.set_xlim(t_min, t_max + 0.5)
        self.ax2.set_xlim(t_min, t_max + 0.5)

        # Auto-scale Y within visible window
        visible_idx = [i for i, t in enumerate(times_list) if t >= t_min]
        if visible_idx:
            vis_g1 = [gsr1_list[i] for i in visible_idx]
            vis_g2 = [gsr2_list[i] for i in visible_idx]

            margin = 50
            y1_min, y1_max = min(vis_g1) - margin, max(vis_g1) + margin
            y2_min, y2_max = min(vis_g2) - margin, max(vis_g2) + margin

            self.ax1.set_ylim(max(0, y1_min), min(4095, y1_max))
            self.ax2.set_ylim(max(0, y2_min), min(4095, y2_max))

            # Stats
            self.stats_text.set_text(
                f"CH1: {gsr1_list[-1]:4d}  (μ={sum(vis_g1)/len(vis_g1):.0f})  |  "
                f"CH2: {gsr2_list[-1]:4d}  (μ={sum(vis_g2)/len(vis_g2):.0f})  |  "
                f"N={len(times_list)}  errors={self.parse_errors}"
            )

        return self.line1, self.line2, self.stats_text

    def run(self):
        """Start the live plot."""
        self.connect()

        # Wait for stream start
        print("Waiting for data stream...")
        print("(If the board doesn't send '# START', will proceed after 10s)")
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

        self.setup_plot()

        self.ani = animation.FuncAnimation(
            self.fig,
            self.update,
            interval=50,  # 20 FPS refresh
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
            print(f"Done. Good samples: {self.good_samples}, Parse errors: {self.parse_errors}")


def main():
    parser = argparse.ArgumentParser(description="GSR Real-Time Plotter")
    parser.add_argument("--port", "-p", required=True, help="Serial port (e.g. COM5)")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate")
    parser.add_argument(
        "--window", "-w", type=float, default=10.0,
        help="Display window in seconds (default: 10)",
    )
    parser.add_argument(
        "--list", "-l", action="store_true", help="List available serial ports",
    )
    args = parser.parse_args()

    if args.list:
        ports = serial.tools.list_ports.comports()
        for p in ports:
            print(f"  {p.device} - {p.description}")
        return

    plotter = GSRPlotter(args.port, args.baud, args.window)
    plotter.run()


if __name__ == "__main__":
    main()
