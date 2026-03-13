<!--
SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
SPDX-License-Identifier: Apache-2.0
-->

# Raspberry Pi + ESP32-C3 Test Rig

Hardware test rig for WallaBMC on ESP32-C3, using a Raspberry Pi as the
managed host. The ESP32-C3 acts as the BMC, managing power/reset and
providing a serial console bridge to the RPi.

## Bill of Materials

| Qty | Part | Notes |
|-----|------|-------|
| 1 | ESP32-C3 DevKitM | Any ESP32-C3 dev board with exposed GPIO |
| 1 | Raspberry Pi (3B+/4/5) | Managed host |
| 4+ | Dupont jumper wires (F-F) | GPIO connections |
| 1 | USB-C cable | ESP32-C3 power and flashing |
| 1 | USB power supply for RPi | Standard RPi PSU |

## Wiring Diagram

Both the RPi and ESP32-C3 operate at 3.3V logic — **no level shifting needed**.

```
ESP32-C3 DevKitM              Raspberry Pi
┌──────────────┐              ┌──────────────┐
│              │              │              │
│  UART1 TX ───┼──────────────┼─→ GPIO15 RXD │  Serial console
│  (GPIO10)    │              │   (pin 10)   │  (console bridge)
│              │              │              │
│  UART1 RX ───┼──────────────┼─← GPIO14 TXD │
│  (GPIO3)     │              │   (pin 8)    │
│              │              │              │
│  GPIO8 ──────┼──────────────┼─→ RUN header │  Power control
│  (power)     │              │   (active-low │  (active-low reset)
│              │              │    reset pin) │
│              │              │              │
│  GND ────────┼──────────────┼── GND        │  Common ground
│  (any GND)   │              │   (pin 6)    │
│              │              │              │
└──────────────┘              └──────────────┘
```

### Pin Assignments (ESP32-C3)

These map to the device tree aliases in `boards/esp32c3_devkitm.overlay`:

| Function | ESP32-C3 GPIO | DT Alias | Notes |
|----------|---------------|----------|-------|
| Status LED | GPIO2 | `status-led` | Onboard LED |
| UART1 TX | GPIO10 | `console-bridge-uart` | → RPi RXD |
| UART1 RX | GPIO3 | `console-bridge-uart` | ← RPi TXD |
| Power control | GPIO8 | `power-gpio-1` | → RPi RUN header |
| Reset | GPIO8 | `reset-gpio` | Shared with power |
| JTAG TCK | GPIO4 | `jtagtck` | Optional |
| JTAG TMS | GPIO5 | `jtagtms` | Optional |
| JTAG TDO | GPIO6 | `jtagtdo` | Optional |
| JTAG TDI | GPIO7 | `jtagtdi` | Optional |
| Reset button | GPIO9 | `reset-button` | Boot button on DevKitM |

> **Note:** Power and reset share GPIO8 for the RPi test rig because
> the RPi RUN header is a single active-low reset pin. For targets with
> separate power and reset signals, use separate GPIOs and update the
> overlay.

### Pin Assignments (Raspberry Pi)

| Function | RPi Pin | RPi GPIO | Notes |
|----------|---------|----------|-------|
| UART RXD | Pin 10 | GPIO15 | ← ESP32-C3 UART1 TX |
| UART TXD | Pin 8 | GPIO14 | → ESP32-C3 UART1 RX |
| RUN reset | RUN header | — | Active-low reset |
| Ground | Pin 6 | — | Common ground |

## Raspberry Pi Serial Console Configuration

Enable the UART serial console on the RPi:

```bash
# Enable serial port, disable serial console login
sudo raspi-config nonint do_serial_hw 0
sudo raspi-config nonint do_serial_cons 1

# Or manually in /boot/firmware/config.txt:
# enable_uart=1

# Reboot to apply
sudo reboot
```

After reboot, the RPi serial console is available at `/dev/ttyS0`
(RPi 3B+/4) or `/dev/ttyAMA0` (RPi 5) at 115200 baud.

The WallaBMC console bridge maps this to TCP port 22 on the ESP32-C3's
IP address: `nc <wallabmc-ip> 22`

## Flashing the ESP32-C3

### Initial Flash (USB)

Connect the ESP32-C3 via USB-C and flash:

```bash
# Build
west build -b esp32c3_devkitm .

# Flash via USB-serial (hold BOOT button, press RESET, release BOOT)
west flash
```

### OTA Updates (future, after MCUboot Phase 5)

Once MCUboot is integrated, firmware updates can be uploaded via the
WallaBMC web interface.

## Network Configuration

### Hostname Convention

```
rpiN-esp32c3.iot.welland.mithis.com
```

Where `N` is the rig number (e.g., `rpi1-esp32c3.iot.welland.mithis.com`).

### First Boot (WiFi Provisioning)

On first boot with no stored WiFi credentials, the ESP32-C3 starts a
SoftAP named `WallaBMC-XXXX`:

1. Connect to the `WallaBMC-XXXX` WiFi network (no password)
2. Open `http://192.168.4.1/provision` in a browser
3. Select the target WiFi network and enter the PSK
4. The ESP32-C3 reboots and connects to WiFi

Alternatively, provision via USB serial:

```
config bmc wifi_ssid MyNetwork
config bmc wifi_psk MyPassword
bmc reboot
```

## Verification

After setup, verify the rig works:

```bash
# Check WiFi connectivity
ping <wallabmc-ip>

# Check web UI
curl http://<wallabmc-ip>/

# Check serial console bridge
nc <wallabmc-ip> 22
# Should see RPi login prompt (if serial console is enabled)

# Check Redfish API
curl http://<wallabmc-ip>/redfish/v1/Systems/1
```
