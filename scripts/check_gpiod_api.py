#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""Probe gpiod API to find correct usage."""
import gpiod
print(f"Version: {gpiod.__version__}")
print(f"Dir: {[x for x in dir(gpiod) if not x.startswith('_')]}")
print()
# Try to read GPIO14 as input
chip = gpiod.Chip("/dev/gpiochip4")
print(f"Chip: {chip.get_info().name}")
# Check LineSettings
if hasattr(gpiod, 'LineSettings'):
    print(f"LineSettings dir: {[x for x in dir(gpiod.LineSettings) if not x.startswith('_')]}")
if hasattr(gpiod.line, 'Direction'):
    print(f"line.Direction: {dir(gpiod.line.Direction)}")
elif hasattr(gpiod, 'line'):
    print(f"gpiod.line dir: {[x for x in dir(gpiod.line) if not x.startswith('_')]}")
# Try reading pin 14
try:
    req = chip.request_lines(
        consumer="test",
        config={14: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)},
    )
    val = req.get_value(14)
    print(f"GPIO14 = {val}")
    req.release()
except Exception as e:
    print(f"Method 1 failed: {e}")
chip.close()
