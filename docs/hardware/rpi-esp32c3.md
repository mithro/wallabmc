<!--
SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
SPDX-License-Identifier: Apache-2.0
-->

# Raspberry Pi 4 + ESP32-C3 Super Mini Test Rig

Hardware test rig for WallaBMC on ESP32-C3, using a Raspberry Pi 4
(`rpi4-esp`) as the USB host and test controller. The ESP32-C3 connects
via USB for flashing/console and via GPIO wires for UART, JTAG, and
general purpose I/O testing.

## Hardware

- **RPi 4** (`rpi4-esp`): Model B Rev 1.4, 8GB, Debian trixie, PoE powered
  - SSH: `tim@ipv4.eth0.rpi4-esp.iot.welland.mithis.com`
  - eth0: `10.1.90.206` (static DHCP, VLAN 90), switch port GSM7252PS 1/0/27
  - wlan0: `10.1.90.207` (ansells-iot WiFi, VLAN 90)
  - ESP32-C3 USB serial: `/dev/ttyESP32C3` (udev symlink, USB path `1-1.2.4`)
  - WiFi AP for testing: `wlanE` (RTL8188CUS USB adapter, unmanaged by NM)
  - esptool: `/opt/esptool/bin/esptool`
  - Full hardware inventory: `~/local/rpi4-esp.md`
- **ESP32-C3**: Tenstar Robot ESP32-C3 Super Mini
  - Chip: ESP32-C3 (QFN32) rev v0.4, single core RISC-V 160MHz
  - Flash: 4MB embedded (XMC)
  - WiFi MAC: `44:1b:f6:2e:a9:a4`
  - USB: built-in USB-Serial/JTAG (`303a:1001`, serial `44:1B:F6:2E:A9:A4`)
  - Exposed pins: 5V, GND, 3V3, GPIO 0-10, 20, 21
  - USB hub: Genesys Logic (ganged power — shares with wlanE, nRF52840, ESP32-CAM)
  - Power cycle: `sudo uhubctl -l 1-1 -p 2 -a cycle -d 3` (affects all hub devices)

### ESP32-C3 Super Mini Pinout

```
        USB-C
     ┌─────────┐
 5V  │●       ●│ GPIO5
 GND │●       ●│ GPIO6
 3V3 │●       ●│ GPIO7
 IO4 │●       ●│ GPIO8
 IO3 │●       ●│ GPIO9 (BOOT)
 IO2 │●       ●│ GPIO10
 IO1 │●       ●│ GPIO20
 IO0 │●       ●│ GPIO21
     └─────────┘
```

GPIO11-17 are used for internal flash and not exposed.

## Wiring

All wires connect to the **outer edge** (even-numbered pins) of the RPi
40-pin GPIO header. Both devices operate at 3.3V — no level shifting needed.

```
RPi Header (even pins, outer edge)          ESP32-C3 Super Mini
┌────────────────────────────────┐          ┌──────────────────┐
│ Pin  2: (empty)                │          │                  │
│ Pin  4: (empty)                │          │                  │
│ Pin  6: GND ───────────────────┼──────────┤ GND              │
│ Pin  8: GPIO14 (TXD) ─Yellow───┼──────────┤ GPIO3  (UART1 RX)│
│ Pin 10: GPIO15 (RXD) ─Green────┼──────────┤ GPIO10 (UART1 TX)│
│ Pin 12: GPIO18 ────────White───┼──────────┤ GPIO5            │
│ Pin 14: (empty)                │          │                  │
│ Pin 16: GPIO23 ────────White───┼──────────┤ GPIO6            │
│ Pin 18: GPIO24 ────────Purple──┼──────────┤ GPIO7            │
│ Pin 20: (empty)                │          │                  │
│ Pin 22: GPIO25 ────────Blue────┼──────────┤ GPIO8            │
│ Pin 24: GPIO8/CE0 ─────Red─────┼──────────┤ GPIO9  (BOOT)    │
│ Pin 26: GPIO7/CE1 ─────Orange──┼──────────┤ GPIO20 (UART0 RX)│
│ Pin 28: GPIO1/ID_SD ───Grey────┼──────────┤ GPIO21 (UART0 TX)│
│ Pin 30: (empty)                │          │                  │
│ Pin 32: GPIO12 ────────White───┼──────────┤ GPIO4            │
│ Pin 34: (empty)                │          │                  │
│ Pin 36: GPIO16 ────────Black───┼──────────┤ GPIO2  (LED)     │
│ Pin 38: GPIO20 ────────Brown───┼──────────┤ GPIO1            │
│ Pin 40: GPIO21 ────────Red─────┼──────────┤ GPIO0            │
└────────────────────────────────┘          └──────────────────┘
```

### Wiring Table

| RPi Pin | RPi GPIO (BCM) | Wire | ESP32-C3 GPIO | Function |
|---------|----------------|------|---------------|----------|
| 6 | GND | — | GND | Common ground |
| 8 | GPIO14 (TXD) | Yellow | 3 | Console bridge: RPi TX → ESP UART1 RX |
| 10 | GPIO15 (RXD) | Green | 10 | Console bridge: ESP UART1 TX → RPi RX |
| 12 | GPIO18 | White | 5 | JTAG TMS |
| 16 | GPIO23 | White | 6 | JTAG TDO |
| 18 | GPIO24 | Purple | 7 | JTAG TDI |
| 22 | GPIO25 | Blue | 8 | General purpose |
| 24 | GPIO8 (CE0) | Red | 9 | BOOT button |
| 26 | GPIO7 (CE1) | Orange | 20 | UART0 RX (ESP console) |
| 28 | GPIO1 (ID_SD) | Grey | 21 | UART0 TX (ESP console) |
| 32 | GPIO12 | White | 4 | JTAG TCK |
| 36 | GPIO16 | Black | 2 | Status LED |
| 38 | GPIO20 | Brown | 1 | General purpose |
| 40 | GPIO21 | Red | 0 | General purpose |

### WallaBMC Device Tree Alias Mapping

| DT Alias | ESP32-C3 GPIO | RPi GPIO (BCM) |
|----------|---------------|----------------|
| `console-bridge-uart` TX | GPIO10 | GPIO15 (RXD) |
| `console-bridge-uart` RX | GPIO3 | GPIO14 (TXD) |
| `jtagtck` | GPIO4 | GPIO12 |
| `jtagtms` | GPIO5 | GPIO18 |
| `jtagtdo` | GPIO6 | GPIO23 |
| `jtagtdi` | GPIO7 | GPIO24 |
| `status-led` | GPIO2 | GPIO16 |
| `reset-button` | GPIO9 (BOOT) | GPIO8 (CE0) |

## Connection Validation

Before running WallaBMC, validate the wiring with dedicated test firmware.

### Step 1: GPIO Toggle Test

Flash the GPIO test firmware (`scripts/gpio_test_fw/`) to the ESP32-C3.
It accepts USB serial commands to set any pin high/low:

```bash
# On the ESP32-C3 serial console:
gpio set 3 1    # Set GPIO3 high
gpio set 3 0    # Set GPIO3 low
```

Then run the RPi validation script:

```bash
# On rpi4-esp:
python3 scripts/validate_gpio_wiring.py /dev/ttyESP32C3
```

This walks through each wired connection, toggles the ESP32-C3 pin, and
verifies the RPi reads the correct value (and no other GPIOs change).

### Step 2: UART Loopback Test

Flash the UART echo firmware. It configures UART1 (TX=GPIO10, RX=GPIO3)
and echoes received bytes:

```bash
# On rpi4-esp:
python3 scripts/validate_uart.py /dev/ttyESP32C3 /dev/ttyAMA0
```

Sends test patterns via the RPi UART and verifies the echo matches.

## Flashing

The ESP32-C3 Super Mini is connected via USB to rpi4-esp:

```bash
# Flash via esptool (on rpi4-esp)
/opt/esptool/bin/esptool --port /dev/ttyESP32C3 --baud 460800 \
    --chip esp32c3 write-flash 0x0 wallabmc-esp32c3.bin

# Or cross-compile locally and scp:
west build -p always -b esp32c3_devkitm .
scp build/zephyr/zephyr.bin rpi4-esp:~/wallabmc-esp32c3.bin
ssh rpi4-esp '/opt/esptool/bin/esptool --port /dev/ttyESP32C3 \
    --baud 460800 --chip esp32c3 write-flash 0x0 ~/wallabmc-esp32c3.bin'
```

### USB Power Cycling

The ESP32-C3 is on a Genesys Logic USB hub with **ganged power switching**
(cycling power affects all devices on the hub including wlanE, nRF52840,
and ESP32-CAM):

```bash
sudo uhubctl -l 1-1 -p 2 -a cycle -d 3
```

## WiFi Testing

### Test AP Setup

The RTL8188CUS USB WiFi adapter (`wlanE`) on rpi4-esp can serve as a
test AP:

```bash
sudo bash scripts/start_test_ap.sh
# Creates open AP "esp-test" on channel 6, DHCP 192.168.4.10-50
```

### WiFi Provisioning via USB Serial

```
config bmc wifi_ssid esp-test
config bmc wifi_psk ""
wifi connect
wifi status
```

### WiFi Provisioning via SoftAP

With no stored WiFi credentials, the ESP32-C3 starts SoftAP
`WallaBMC-A9A4` (last 4 hex of MAC `44:1b:f6:2e:a9:a4`):

```bash
# From rpi4-esp, connect to the SoftAP:
sudo iw dev wlanE connect "WallaBMC-A9A4"
sudo ip addr add 192.168.4.100/24 dev wlanE

# Access provisioning page:
curl http://192.168.4.1/provision

# Submit credentials via API:
curl -X POST http://192.168.4.1/api/provision \
    -H 'Content-Type: application/json' \
    -d '{"ssid":"esp-test","psk":""}'
```

## Serial Console

The ESP32-C3 uses a built-in USB-Serial/JTAG interface for its console.
Unlike a standard UART, **the DTR and RTS signals control the chip's
reset and boot mode pins**. Most serial terminal tools (picocom, screen,
minicom, pyserial) toggle DTR when opening the port, which resets the
chip and may cause it to boot into ESP-IDF download mode instead of
running the application.

### Recommended: esptool monitor

```bash
/opt/esptool/bin/esptool --port /dev/ttyESP32C3 monitor
```

This is the safest way to interact with the console — esptool knows
how to handle the USB-Serial/JTAG interface without triggering resets.

### Alternative: pyserial with DTR disabled

```python
import serial
s = serial.Serial('/dev/ttyESP32C3', 115200, dsrdtr=False, rtscts=False)
# Do NOT set s.dtr or s.rts — any toggle resets the chip
```

### Known behaviour after reset

If the chip is reset via DTR toggle (rather than a clean power cycle),
the ROM bootloader may enter ESP-IDF boot mode instead of Zephyr simple
boot. This causes a different application to run. To recover, re-flash
with esptool (which handles the reset sequence correctly).

## Verification

```bash
# Check WiFi connectivity
ping <wallabmc-ip>

# Check web UI
curl --compressed http://<wallabmc-ip>/

# Check serial console bridge (requires UART wiring validated)
nc <wallabmc-ip> 22

# Check Redfish API
curl --compressed -u admin:admin http://<wallabmc-ip>/redfish/v1/
```
