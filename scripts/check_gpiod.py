#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""Check if gpiod is available and list GPIO chips."""
try:
    import gpiod
    print(f"gpiod version: {gpiod.__version__}")
    for path in ["/dev/gpiochip0", "/dev/gpiochip4"]:
        try:
            chip = gpiod.Chip(path)
            info = chip.get_info()
            print(f"{path}: {info.name} ({info.num_lines} lines)")
            chip.close()
        except Exception as e:
            print(f"{path}: {e}")
except ImportError:
    print("gpiod NOT installed. Run: sudo apt install python3-gpiod")
