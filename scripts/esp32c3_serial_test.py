#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
ESP32-C3 serial shell test — sends commands and captures output.

Usage: python3 esp32c3_serial_test.py /dev/ttyESP32C3 [command ...]

If no commands given, sends a newline + "config show" as a smoke test.
"""

import serial
import sys
import time


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyESP32C3"
    commands = sys.argv[2:] if len(sys.argv) > 2 else ["config show"]

    ser = serial.Serial(port, 115200, timeout=1)

    # Flush any pending data
    ser.reset_input_buffer()

    # Send newline to trigger prompt
    ser.write(b"\r\n")
    time.sleep(0.5)
    data = ser.read(4096)
    print(data.decode("utf-8", errors="replace"), end="")

    for cmd in commands:
        print(f"\n--- Sending: {cmd} ---")
        ser.write(f"{cmd}\r\n".encode())
        time.sleep(1)
        data = ser.read(4096)
        print(data.decode("utf-8", errors="replace"), end="")

    ser.close()
    print("\n--- Done ---")


if __name__ == "__main__":
    main()
