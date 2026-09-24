"""Capture one firmware recording into a timestamped CSV file."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import time

import serial


def send_command(port: serial.Serial, command: str) -> None:
    """Send a full command line using the firmware's ring-buffer UART RX."""
    port.write((command + "\r\n").encode("ascii"))
    port.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Record pivot telemetry to CSV")
    parser.add_argument("--port", default="COM6")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[1]
    output_dir = project_dir / "recordings"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.csv"

    with serial.Serial(args.port, args.baud, timeout=0.2) as port:
        time.sleep(2.0)
        port.reset_input_buffer()
        send_command(port, "record 1")

        with output_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(["time_sec", "position_mm", "angle_deg"])
            row_count = 0
            print(f"Recording to {output_path}")
            print("Press Ctrl+C to stop.")

            try:
                while True:
                    line = port.readline().decode("ascii", errors="ignore").strip()
                    if not line or line == "time_sec,position_mm,angle_deg":
                        continue
                    fields = line.split(",")
                    if len(fields) != 3:
                        continue
                    if not fields[0] or not fields[1] or not fields[2]:
                        continue
                    writer.writerow(fields)
                    output.flush()
                    row_count += 1
            except KeyboardInterrupt:
                pass
            finally:
                send_command(port, "record 0")

    if row_count == 0:
        print(f"No telemetry rows were received. Check that {args.port} is the Nucleo port and that the board is connected.")
    else:
        print(f"Saved {row_count} rows to {output_path}")


if __name__ == "__main__":
    main()