#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Validate GPIO wiring between ESP32-C3 and Raspberry Pi.

Requires:
- GPIO test firmware flashed on ESP32-C3 (scripts/gpio_test_fw/)
- gpiod Python library on the RPi (apt install python3-gpiod)
- Run on rpi4-esp as: python3 validate_gpio_wiring.py /dev/ttyESP32C3

For each wired connection:
1. ESP32-C3 drives pin HIGH via serial command
2. RPi reads corresponding GPIO — should be HIGH
3. Verify no other wired RPi GPIOs changed
4. ESP32-C3 drives pin LOW
5. RPi reads — should be LOW
"""

import sys
import time
import serial

try:
    import gpiod
except ImportError:
    print("ERROR: gpiod not available. Install with: sudo apt install python3-gpiod")
    sys.exit(1)


# Wiring table: (esp32_gpio, rpi_bcm_gpio, wire_color, description)
WIRING = [
    (3,  14, "Yellow", "UART1 RX (console bridge)"),
    (10, 15, "Green",  "UART1 TX (console bridge)"),
    (5,  18, "White",  "JTAG TMS"),
    (6,  23, "White",  "JTAG TDO"),
    (7,  24, "Purple", "JTAG TDI"),
    (8,  25, "Blue",   "General purpose"),
    (9,   8, "Red",    "BOOT button"),
    (20,  7, "Orange", "UART0 RX"),
    (21,  1, "Grey",   "UART0 TX"),
    (4,  12, "White",  "JTAG TCK"),
    (2,  16, "Black",  "Status LED"),
    (1,  20, "Brown",  "General purpose"),
    (0,  21, "Red",    "General purpose"),
]

# All RPi BCM GPIOs that are wired
ALL_RPI_GPIOS = sorted(set(rpi for _, rpi, _, _ in WIRING))


def send_esp_cmd(ser, cmd):
    """Send a command to ESP32-C3 and return response lines."""
    ser.reset_input_buffer()
    ser.write(f"{cmd}\r\n".encode())
    time.sleep(0.3)
    data = ser.read(4096).decode("utf-8", errors="replace")
    return data


def read_rpi_gpio(chip, pin):
    """Read a single RPi GPIO pin value."""
    request = chip.request_lines(
        consumer="gpio-test",
        config={pin: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)},
    )
    val = request.get_value(pin)
    request.release()
    return int(val == gpiod.line.Value.ACTIVE)


def read_all_rpi_gpios(chip):
    """Read all wired RPi GPIOs, return dict of pin -> value."""
    values = {}
    for pin in ALL_RPI_GPIOS:
        values[pin] = read_rpi_gpio(chip, pin)
    return values


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyESP32C3"

    print(f"=== GPIO Wiring Validation ===")
    print(f"ESP32-C3 serial: {port}")
    print(f"Wired connections: {len(WIRING)}")
    print()

    ser = serial.Serial(port, 115200, timeout=1)

    # Wait for shell prompt
    ser.write(b"\r\n")
    time.sleep(0.5)
    ser.read(4096)

    # Verify ESP32-C3 is running GPIO test firmware
    resp = send_esp_cmd(ser, "gpio all_in")
    if "pin" not in resp.lower():
        print(f"ERROR: ESP32-C3 does not seem to have GPIO test firmware")
        print(f"Response: {resp}")
        ser.close()
        sys.exit(1)

    print("ESP32-C3 GPIO test firmware detected")

    # Open RPi GPIO chip
    chip = gpiod.Chip("/dev/gpiochip4")  # RPi 4 main GPIO
    print(f"RPi GPIO chip: {chip.get_info().name}")
    print()

    # First, set all ESP32 pins LOW to establish baseline
    send_esp_cmd(ser, "gpio all_out 0")
    time.sleep(0.2)
    baseline = read_all_rpi_gpios(chip)
    print(f"Baseline (all ESP pins LOW): {baseline}")
    print()

    passed = 0
    failed = 0

    for esp_pin, rpi_pin, color, desc in WIRING:
        print(f"--- Testing ESP GPIO{esp_pin} → RPi GPIO{rpi_pin} ({color}, {desc}) ---")

        # Set ESP pin HIGH
        send_esp_cmd(ser, f"gpio set {esp_pin} 1")
        time.sleep(0.1)

        # Read all RPi GPIOs
        values_high = read_all_rpi_gpios(chip)

        # Check target pin is HIGH
        target_high = values_high[rpi_pin]

        # Check no other pins changed
        others_changed = []
        for other_rpi in ALL_RPI_GPIOS:
            if other_rpi == rpi_pin:
                continue
            if values_high[other_rpi] != baseline[other_rpi]:
                others_changed.append(
                    f"RPi GPIO{other_rpi} was {baseline[other_rpi]}, now {values_high[other_rpi]}"
                )

        # Set ESP pin LOW
        send_esp_cmd(ser, f"gpio set {esp_pin} 0")
        time.sleep(0.1)

        # Read target pin — should be LOW again
        values_low = read_all_rpi_gpios(chip)
        target_low = values_low[rpi_pin]

        # Report
        ok = True
        if target_high != 1:
            print(f"  FAIL: RPi GPIO{rpi_pin} did not go HIGH (got {target_high})")
            ok = False
        if target_low != 0:
            print(f"  FAIL: RPi GPIO{rpi_pin} did not go LOW (got {target_low})")
            ok = False
        if others_changed:
            for msg in others_changed:
                print(f"  WARN: Unexpected change: {msg}")
            ok = False

        if ok:
            print(f"  PASS")
            passed += 1
        else:
            failed += 1

        # Update baseline with current state
        baseline = values_low

    print()
    print(f"=== Results: {passed} passed, {failed} failed out of {len(WIRING)} ===")

    ser.close()
    chip.close()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
