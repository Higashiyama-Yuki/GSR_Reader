"""
GSR Serial Receiver
Reads CSV data from ESP32C3 over serial and saves to file.

Usage:
    python receiver.py --port COM5
    python receiver.py --port COM5 --output data/my_session.csv
    python receiver.py --list   # List available ports
"""

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports


def list_ports():
    """List available serial ports."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return
    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device} - {p.description}")


def create_output_path(output_arg: str | None) -> Path:
    """Create output file path with timestamp if not specified."""
    if output_arg:
        path = Path(output_arg)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(f"data/session_{ts}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def receive(port: str, baud: int, output: Path, duration: float | None):
    """Main receive loop."""
    print(f"Connecting to {port} @ {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except serial.SerialException as e:
        print(f"Error opening {port}: {e}")
        sys.exit(1)

    print(f"Saving to: {output}")
    if duration:
        print(f"Recording for {duration}s")
    print("Press Ctrl+C to stop.\n")

    start_time = time.time()
    sample_count = 0
    header_done = False

    with open(output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "gsr1_raw", "gsr2_raw"])

        try:
            while True:
                if duration and (time.time() - start_time) > duration:
                    print(f"\nDuration reached ({duration}s).")
                    break

                line = ser.readline().decode("utf-8", errors="replace").strip()

                if not line:
                    continue

                # Skip header/comment lines from firmware
                if line.startswith("#"):
                    if "START" in line:
                        header_done = True
                        print("Stream started.")
                    continue

                # Parse CSV: timestamp_ms,gsr1,gsr2
                parts = line.split(",")
                if len(parts) != 3:
                    continue

                try:
                    ts = int(parts[0])
                    gsr1 = int(parts[1])
                    gsr2 = int(parts[2])
                except ValueError:
                    continue

                writer.writerow([ts, gsr1, gsr2])
                sample_count += 1

                # Progress feedback every 100 samples
                if sample_count % 100 == 0:
                    elapsed = time.time() - start_time
                    rate = sample_count / elapsed if elapsed > 0 else 0
                    print(
                        f"\r  Samples: {sample_count}  "
                        f"Rate: {rate:.1f} Hz  "
                        f"GSR1: {gsr1:4d}  GSR2: {gsr2:4d}",
                        end="",
                    )

        except KeyboardInterrupt:
            print("\n\nStopped by user.")

    elapsed = time.time() - start_time
    print(f"\nTotal: {sample_count} samples in {elapsed:.1f}s")
    print(f"Saved to: {output}")
    ser.close()


def main():
    parser = argparse.ArgumentParser(description="GSR Serial Receiver")
    parser.add_argument("--port", "-p", help="Serial port (e.g. COM5)")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate")
    parser.add_argument("--output", "-o", help="Output CSV file path")
    parser.add_argument(
        "--duration", "-d", type=float, help="Recording duration in seconds"
    )
    parser.add_argument(
        "--list", "-l", action="store_true", help="List available serial ports"
    )
    args = parser.parse_args()

    if args.list:
        list_ports()
        return

    if not args.port:
        list_ports()
        print("\nSpecify a port with --port")
        sys.exit(1)

    output = create_output_path(args.output)
    receive(args.port, args.baud, output, args.duration)


if __name__ == "__main__":
    main()
