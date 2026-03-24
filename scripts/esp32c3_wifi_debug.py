#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
ESP32-C3 WiFi debug — send commands and capture log for longer.
"""

import serial
import sys
import time


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyESP32C3"

    ser = serial.Serial(port, 115200, timeout=0.5)
    ser.reset_input_buffer()

    # Send newline to trigger prompt
    ser.write(b"\r\n")
    time.sleep(0.3)
    ser.read(4096)  # discard prompt

    # Send wifi disconnect first to clear state
    print("--- Sending: wifi disconnect ---")
    ser.write(b"wifi disconnect\r\n")
    time.sleep(1)
    data = ser.read(4096)
    print(data.decode("utf-8", errors="replace"), end="")

    # Now scan
    print("\n--- Sending: wifi scan ---")
    ser.write(b"wifi scan\r\n")

    # Read for 10 seconds to capture scan results
    end_time = time.time() + 10
    while time.time() < end_time:
        data = ser.read(4096)
        if data:
            print(data.decode("utf-8", errors="replace"), end="")

    # Now try connect
    print("\n--- Sending: wifi connect ---")
    ser.write(b"wifi connect\r\n")

    # Read for 15 seconds to capture connect result
    end_time = time.time() + 15
    while time.time() < end_time:
        data = ser.read(4096)
        if data:
            print(data.decode("utf-8", errors="replace"), end="")

    # Check status
    print("\n--- Sending: wifi status ---")
    ser.write(b"wifi status\r\n")
    time.sleep(1)
    data = ser.read(4096)
    print(data.decode("utf-8", errors="replace"), end="")

    ser.close()
    print("\n--- Done ---")


if __name__ == "__main__":
    main()
