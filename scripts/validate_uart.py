#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Validate UART wiring between ESP32-C3 UART1 and RPi serial port.

Requires:
- UART echo firmware flashed on ESP32-C3 (scripts/uart_test_fw/)
- Run on rpi4-esp as:
    python3 validate_uart.py /dev/ttyESP32C3 /dev/ttyAMA0

The script:
1. Sends test patterns via RPi UART TX (GPIO14 → ESP GPIO3)
2. Reads echo back on RPi UART RX (ESP GPIO10 → GPIO15)
3. Verifies sent data matches received data
"""

import sys
import time
import serial


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 validate_uart.py <esp32_usb_port> <rpi_uart_port>")
        print("  esp32_usb_port: USB serial to ESP32-C3 (e.g. /dev/ttyESP32C3)")
        print("  rpi_uart_port:  RPi hardware UART (e.g. /dev/ttyAMA0)")
        sys.exit(1)

    esp_port = sys.argv[1]
    rpi_port = sys.argv[2]

    print(f"=== UART Wiring Validation ===")
    print(f"ESP32-C3 USB serial: {esp_port}")
    print(f"RPi hardware UART:   {rpi_port}")
    print()

    # Open ESP32-C3 USB serial for shell commands
    esp_ser = serial.Serial(esp_port, 115200, timeout=1)
    esp_ser.write(b"\r\n")
    time.sleep(0.3)
    esp_ser.read(4096)  # flush

    # Verify firmware
    esp_ser.write(b"uart status\r\n")
    time.sleep(0.3)
    resp = esp_ser.read(4096).decode("utf-8", errors="replace")
    if "echo" not in resp.lower():
        print("ERROR: ESP32-C3 does not have UART echo firmware")
        print(f"Response: {resp}")
        esp_ser.close()
        sys.exit(1)
    print("ESP32-C3 UART echo firmware detected")

    # Ensure echo is enabled
    esp_ser.write(b"uart start\r\n")
    time.sleep(0.2)
    esp_ser.read(4096)

    # Open RPi hardware UART
    rpi_ser = serial.Serial(rpi_port, 115200, timeout=2)
    rpi_ser.reset_input_buffer()

    passed = 0
    failed = 0

    # Test 1: Single byte
    test_patterns = [
        ("single byte", b"A"),
        ("alphabet", b"abcdefghijklmnopqrstuvwxyz"),
        ("digits", b"0123456789"),
        ("binary pattern", bytes(range(0x20, 0x7f))),
        ("repeated", b"X" * 100),
    ]

    for name, pattern in test_patterns:
        print(f"\n--- Test: {name} ({len(pattern)} bytes) ---")

        rpi_ser.reset_input_buffer()
        rpi_ser.write(pattern)
        rpi_ser.flush()

        # Wait for echo
        time.sleep(0.5)

        received = rpi_ser.read(len(pattern) + 10)

        if received == pattern:
            print(f"  PASS: sent {len(pattern)}, received {len(received)} bytes, match")
            passed += 1
        else:
            print(f"  FAIL: sent {len(pattern)}, received {len(received)} bytes")
            if len(received) == 0:
                print(f"  No data received — check wiring!")
            else:
                # Show first difference
                for i in range(min(len(pattern), len(received))):
                    if i >= len(received) or pattern[i] != received[i]:
                        print(f"  First difference at byte {i}: "
                              f"sent 0x{pattern[i]:02x}, got 0x{received[i]:02x}")
                        break
                if len(received) != len(pattern):
                    print(f"  Length mismatch: sent {len(pattern)}, got {len(received)}")
            failed += 1

    # Test 2: ESP32 sends to RPi
    print(f"\n--- Test: ESP32 → RPi (uart send command) ---")
    rpi_ser.reset_input_buffer()
    esp_ser.reset_input_buffer()
    esp_ser.write(b"uart send HELLO_FROM_ESP\r\n")
    time.sleep(0.5)

    received = rpi_ser.read(100)
    received_str = received.decode("utf-8", errors="replace").strip()

    if "HELLO_FROM_ESP" in received_str:
        print(f"  PASS: received '{received_str}'")
        passed += 1
    else:
        print(f"  FAIL: expected 'HELLO_FROM_ESP', got '{received_str}'")
        if len(received) == 0:
            print(f"  No data received — check TX wiring (ESP GPIO10 → RPi GPIO15)")
        failed += 1

    print()
    print(f"=== Results: {passed} passed, {failed} failed out of {passed + failed} ===")

    # Show ESP counters
    esp_ser.write(b"uart status\r\n")
    time.sleep(0.3)
    resp = esp_ser.read(4096).decode("utf-8", errors="replace")
    for line in resp.splitlines():
        if "bytes" in line.lower():
            print(f"  ESP32: {line.strip()}")

    esp_ser.close()
    rpi_ser.close()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
