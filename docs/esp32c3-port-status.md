<!--
SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
SPDX-License-Identifier: Apache-2.0
-->

# ESP32-C3 Port Status

**Date:** 2026-03-25
**Branch:** `esp32c3-port` on `mithro/wallabmc`
**Commits:** 22 commits ahead of `tenstorrent/wallabmc:main`
**Files changed:** 36 files, +2481/-37 lines

## What is WallaBMC?

WallaBMC is a Zephyr RTOS-based Board Management Controller (BMC) firmware.
It provides remote management of a host system via:

- **Web UI** — browser-based dashboard at port 80
- **Redfish API** — DMTF standard REST API for system management
- **Console bridge** — UART-to-TCP bridge on port 22 for remote serial console
- **JTAG** — GPIO bit-bang JTAG daemon for host debugging
- **Power/reset control** — GPIO-driven power sequencing
- **Configuration** — NVS (Non-Volatile Storage) for persistent settings

The firmware supports multiple boards:
- **nucleo_f767zi** — STM32 F767ZI (ARM Cortex-M7), primary development target
- **hifive_premier_p550_mcu** — SiFive P550 (RISC-V), Tenstorrent use case
- **qemu_cortex_m3** — QEMU emulation for CI testing
- **esp32c3_devkitm** — Espressif ESP32-C3 (RISC-V), **this port**

## What this branch does

This branch adds ESP32-C3 support to WallaBMC, including WiFi connectivity
(the ESP32-C3 has no Ethernet), WiFi provisioning via SoftAP captive portal,
and all the portability fixes needed to build the existing codebase for a
non-STM32 platform.

## Architecture of the changes

### Portability fixes (affect all boards)

1. **`src/console_bridge.c`** — Changed from `DT_NODELABEL(usart6)` (STM32-specific)
   to `DT_ALIAS(console_bridge_uart)` so each board's overlay maps its own UART.

2. **`boards/*.overlay`** — Added `console-bridge-uart = &usart6` alias to both
   existing board overlays (nucleo_f767zi, hifive_premier_p550_mcu).

3. **`Kconfig` + `CMakeLists.txt`** — Made `CODE_DATA_RELOCATION_SRAM` conditional
   on `SOC_FLASH_STM32`. STM32 needs flash driver relocated to RAM for safe NVS
   writes (XIP conflict), but ESP32-C3 doesn't. Removed the `FATAL_ERROR` that
   blocked non-STM32 boards from using persistent storage.

4. **`src/rtc.h`** — Fixed `time_set_from_iso_str()` stub to accept `const char *`
   parameter (was `void`), fixing a build error when `CONFIG_RTC=n`.

### ESP32-C3 board support

- **`boards/esp32c3_devkitm.overlay`** — Device tree overlay with:
  - USB-Serial/JTAG as console (`zephyr,console = &usb_serial`)
  - UART1 pinctrl (TX=GPIO10, RX=GPIO3) for console bridge
  - GPIO nodes for JTAG (GPIO4-7), status LED (GPIO2), reset button (GPIO9)
  - Uses default Espressif 4MB flash partition layout (64KB boot + 1792KB app + 192KB NVS)

- **`boards/esp32c3_devkitm.conf`** — Board config:
  - `CONFIG_APP_HTTPS=n` (no TLS — saves significant RAM)
  - `CONFIG_RTC=n` (ESP32-C3 has no RTC peripheral in Zephyr)
  - `CONFIG_WIFI=y`, `CONFIG_APP_WIFI=y`, `CONFIG_APP_WIFI_PROVISION=y`
  - `CONFIG_CONSOLE_BRIDGE=y`, `CONFIG_JTAG=y`, `CONFIG_PERSISTENT_STORAGE=y`
  - Reduced buffer counts: `NET_BUF_*_COUNT=32`, `HTTP_SERVER_MAX_CLIENTS=4`

- **`CMakeLists.txt`** — ESP32-C3 added to non-sysbuild board list (MCUboot
  not yet validated for Espressif bootloader chain).

- **`sysbuild/mcuboot/boards/esp32c3_devkitm.overlay`** — MCUboot overlay
  prepared but not enabled. Espressif MCUboot uses a different bootloader chain
  than STM32/SiFive and needs investigation before enabling.

### WiFi module (`src/wifi.c`, `src/wifi.h`)

Thin shim over Zephyr's `net_mgmt` WiFi API:

- `wifi_init()` — registers event callbacks, auto-connects if credentials stored
- `wifi_connect(ssid, psk)` — sends `NET_REQUEST_WIFI_CONNECT`
- `wifi_disconnect()` — sends `NET_REQUEST_WIFI_DISCONNECT`, cancels reconnect
- Auto-reconnect on disconnect (5 second delay via `k_work_delayable`)
- Shell commands: `wifi connect [ssid [psk]]`, `wifi disconnect`, `wifi scan`, `wifi status`
- `wifi_is_connected()` checks actual interface state (carrier + IPv4 address)
  rather than relying solely on event callbacks (see known issues)

**Kconfig:** `CONFIG_APP_WIFI` (depends on `WIFI`, selects `NET_L2_WIFI_MGMT`,
`NET_MGMT_EVENT`, `NET_MGMT_EVENT_INFO`)

### WiFi config (`src/config.c`, `src/config.h`)

Three new NVS config entries (IDs 11-13):
- `CFG_WIFI_SSID` (string, max 32 chars)
- `CFG_WIFI_PSK` (string, max 64 chars)
- `CFG_WIFI_AUTOCONNECT` (uint8_t, default 1)

Shell commands: `config bmc wifi_ssid <ssid>`, `config bmc wifi_psk <psk>`,
`config bmc wifi_autoconnect <enable|disable>`

Accessor functions: `config_wifi_ssid()`, `config_wifi_psk()`,
`config_wifi_autoconnect()` with `#else` stubs returning empty/false when
`CONFIG_APP_WIFI` is not set.

### WiFi provisioning (`src/wifi_provision.c`, `src/wifi_provision.h`)

SoftAP captive portal for first-time WiFi setup:

- When no SSID is stored in NVS, starts a SoftAP named `WallaBMC-XXXX`
  (last 4 hex digits of the interface MAC address)
- Assigns static IP `192.168.4.1/24` to the SoftAP interface
- Serves provisioning page at `GET /provision` (gzip-compressed static HTML)
- `POST /api/provision` accepts `{"ssid":"...","psk":"..."}` JSON, saves to
  NVS, and triggers a reboot after 2 seconds
- `GET /api/provision/scan` triggers a WiFi scan and returns results as JSON

**Note:** Clients connecting to the SoftAP must set a static IP in the
192.168.4.0/24 subnet — there is no DHCP server on the ESP32-C3 in AP mode.

**Kconfig:** `CONFIG_APP_WIFI_PROVISION` (depends on `APP_WIFI`, selects
`JSON_LIBRARY`)

### CI changes (`.github/workflows/build.yml`)

- `esp32c3_devkitm` added to build matrix (build-only, no runtime test)
- Uses same non-sysbuild path as `qemu_cortex_m3`
- Firmware artifacts uploaded as `zephyr.bin` + `zephyr.elf`

### Boot sequence (`src/main.c`)

Init order with WiFi additions:

```
RTC → Filesystem → Config → Button → WiFi → WiFi Provisioning → Network →
Power → Reset → LED → JTAG → HTTP → Console Bridge → boot_finished
```

WiFi and provisioning init before `net_init()`. This order is required —
moving WiFi after net_init breaks auto-connect (the WiFi driver needs to
be initialized before the network stack configures the interface).

## Build environment

### Prerequisites

- Zephyr SDK 0.17.0+ (ARM + RISC-V toolchains)
- `west` (Python package)
- `esptool` 5.0.2+ (for ESP32-C3 flashing)
- Espressif binary blobs: `west blobs fetch hal_espressif`

### Build commands

```bash
# Set up workspace (one time)
pip install west
mkdir workspace && cd workspace
west init -l ../wallabmc
west update --narrow -o=--depth=10000 -o=--tags
west blobs fetch hal_espressif

# Build for ESP32-C3
west build -p always -b esp32c3_devkitm ../wallabmc

# Build for QEMU (CI testing)
west build -p always -b qemu_cortex_m3 ../wallabmc

# Build for hardware boards (with MCUboot)
west build --sysbuild -p always -b nucleo_f767zi ../wallabmc
west build --sysbuild -p always -b hifive_premier_p550_mcu ../wallabmc

# Run QEMU CI test
python3 ../wallabmc/scripts/run_qemu_ci.py build/zephyr/zephyr.elf
```

### Flash ESP32-C3

```bash
scp build/zephyr/zephyr.bin rpi4-esp:~/wallabmc-esp32c3.bin
ssh rpi4-esp '/opt/esptool/bin/esptool --port /dev/ttyESP32C3 \
    --baud 460800 --chip esp32c3 write-flash 0x0 ~/wallabmc-esp32c3.bin'
```

## Test hardware

### rpi4-esp (Raspberry Pi 4)

- **SSH:** `tim@ipv4.eth0.rpi4-esp.iot.welland.mithis.com` (eth0: `10.1.90.206`)
- **OS:** Debian trixie (aarch64)
- **ESP32-C3 serial:** `/dev/ttyESP32C3` (udev symlink, USB path `1-1.2.4`)
- **WiFi adapter:** `wlanE` (RTL8188CUS, unmanaged by NetworkManager)
- **Power cycle:** `sudo uhubctl -l 1-1 -p 2 -a cycle -d 3` (ganged — affects
  all devices on Genesys hub: ESP32-C3, wlanE, nRF52840, ESP32-CAM)
- **esptool:** `/opt/esptool/bin/esptool`
- **Full inventory:** `/home/tim/local/rpi4-esp.md`

### ESP32-C3 board

- **Board:** Tenstar Robot ESP32-C3 Super Mini
- **Chip:** ESP32-C3 (QFN32) rev v0.4, RISC-V single core 160MHz
- **Flash:** 4MB embedded (XMC)
- **WiFi MAC:** `44:1b:f6:2e:a9:a4`
- **Exposed GPIOs:** 0-10, 20, 21 (GPIO11-17 used for internal flash)

### GPIO wiring (verified 13/13 pass)

All wires on RPi outer edge (even pins). See `docs/hardware/rpi-esp32c3.md`
for full wiring diagram with wire colours.

| RPi Pin | RPi GPIO | ESP32 GPIO | Function |
|---------|----------|------------|----------|
| 6 | GND | GND | Common ground |
| 8 | GPIO14 | 3 | Console bridge: RPi TX → ESP UART1 RX |
| 10 | GPIO15 | 10 | Console bridge: ESP UART1 TX → RPi RX |
| 12 | GPIO18 | 5 | JTAG TMS |
| 16 | GPIO23 | 6 | JTAG TDO |
| 18 | GPIO24 | 7 | JTAG TDI |
| 22 | GPIO25 | 8 | General purpose |
| 24 | GPIO8 | 9 | BOOT button |
| 26 | GPIO7 | 20 | UART0 RX |
| 28 | GPIO1 | 21 | UART0 TX |
| 32 | GPIO12 | 4 | JTAG TCK |
| 36 | GPIO16 | 2 | Status LED |
| 38 | GPIO20 | 1 | General purpose |
| 40 | GPIO21 | 0 | General purpose |

### RPi serial port configuration

The RPi's `/dev/ttyS0` (mini-UART on GPIO14/15) must NOT have a serial
console attached. The following changes were made to rpi4-esp:

- Removed `console=serial0,115200` from `/boot/firmware/cmdline.txt`
- Disabled `serial-getty@ttyS0.service`
- `enable_uart=1` remains in `/boot/firmware/config.txt`

Without these changes, the kernel console and getty eat UART data, making
the console bridge UART inoperable.

## What has been verified

### Build verification (all pass)

| Board | Build type | Result |
|-------|-----------|--------|
| `qemu_cortex_m3` | non-sysbuild | Pass (44% flash, 83% RAM) |
| `esp32c3_devkitm` | non-sysbuild | Pass (18% flash, 59% dram) |
| `nucleo_f767zi` | sysbuild | Pass |
| `hifive_premier_p550_mcu` | sysbuild | Pass |

### QEMU runtime test (pass)

`scripts/run_qemu_ci.py` — boots WallaBMC in QEMU, waits for DHCP,
connects via telnet, sends `bmc poweroff`, verifies shutdown message.

### Hardware verification on ESP32-C3

| Test | Result | Notes |
|------|--------|-------|
| Boot + shell prompt | **Pass** | WallaBMC banner, shell responsive over USB |
| `config show` | **Pass** | All fields display correctly |
| NVS persistence | **Pass** | `config bmc hostname test-esp32c3` survives reboot |
| WiFi connect | **Pass** | Connects to hostapd AP, DHCP bound, gets IP |
| HTTP web UI over WiFi | **Pass** | `curl --compressed http://192.168.4.25/` returns HTML |
| Redfish API over WiFi | **Pass** | `curl http://192.168.4.25/redfish/v1/` returns JSON |
| SoftAP broadcast | **Pass** | `WallaBMC-5CAC` visible in scan |
| Provisioning page | **Pass** | `curl http://192.168.4.1/provision` returns HTML |
| Provisioning POST | **Pass** | `POST /api/provision` returns `{"status":"ok"}` |
| Credentials persist | **Pass** | SSID saved in NVS after provisioning + reboot |
| GPIO wiring | **Pass** | 13/13 connections validated (toggle + read) |
| UART wiring | **Pass** | 6/6 tests (echo + send), both directions |

### Test WiFi AP setup

The RTL8188CUS adapter (`wlanE`) on rpi4-esp runs as a test AP:

```bash
sudo bash ~/start_test_ap.sh
# Creates open AP "esp-test" on channel 6, DHCP range 192.168.4.10-50
```

Script is at `scripts/start_test_ap.sh` in the repo.

## Known issues

### 1. WiFi connect event callback does not fire reliably

**Symptom:** `wifi status` shows "connecting" even when WiFi is fully
connected (carrier=ON, DHCP bound, HTTP serving).

**Root cause:** The `NET_EVENT_WIFI_CONNECT_RESULT` callback registered in
`wifi_event_handler()` never fires on the ESP32-C3. This appears to be a
Zephyr ESP32 WiFi driver issue — the driver successfully connects but does
not emit the management event.

**Workaround:** `wifi_is_connected()` in `wifi.c` checks actual interface
state (`net_if_is_carrier_ok()` + IPv4 address present) instead of relying
on the callback flag. The `wifi status` command uses this function.

**Impact:** The auto-reconnect logic (which triggers on disconnect events)
may not work correctly since disconnect events may also not fire. The
`wifi_connected` flag used elsewhere in the code may be stale.

**To investigate:**
- Check if the ESP32 WiFi driver in `modules/hal/espressif` actually calls
  `wifi_mgmt_raise_connect_result_event()`
- Compare with how `NET_L2_WIFI_SHELL` (Zephyr's built-in wifi shell) handles
  connection status — it may use a different mechanism
- Test with `CONFIG_NET_MGMT_EVENT_LOG_LEVEL_DBG=y` to see if events are emitted

### 2. Boot-time WiFi auto-connect sometimes fails to get DHCP

**Symptom:** After power cycle, WiFi associates (carrier=ON) but DHCP stays
at "selecting" forever. Manual `wifi disconnect` + `wifi connect` fixes it.

**Root cause:** Suspected timing issue. `wifi_init()` calls `wifi_connect()`
during boot, which starts WiFi association. The DHCP client begins sending
discovers before the WiFi link is fully established. The discovers are lost,
and the DHCP client's exponential backoff means it takes very long to retry.

**Workaround:** Manual disconnect + reconnect after boot.

**To investigate:**
- Add a delay between `wifi_connect()` and when DHCP starts
- Or restart DHCP when WiFi connect event fires (if event issue is fixed)
- Or use `CONFIG_ESP32_WIFI_STA_AUTO_DHCPV4=y` which is already set — check
  if the ESP32 WiFi driver has its own DHCP integration that conflicts with
  Zephyr's DHCP client
- Check if `CONFIG_NET_DHCPV4_INITIAL_DELAY_MAX` can be tuned

### 3. SoftAP has no DHCP server

**Symptom:** Clients connecting to the WallaBMC SoftAP must manually
configure a static IP in the 192.168.4.0/24 subnet.

**Root cause:** Zephyr has no built-in DHCP server in our config. The
ESP32-C3 assigns itself 192.168.4.1 but doesn't serve DHCP.

**Impact:** The provisioning flow works from the RPi (which can set a static
IP via script) but would not work from a phone/laptop without manual IP
configuration, making the "captive portal" experience poor.

**To fix:** Either add a Zephyr DHCP server (`CONFIG_NET_DHCPV4_SERVER` if
available) or use a simpler approach like DNS hijacking + static IP
instructions on the provisioning page.

### 4. MCUboot not yet validated for ESP32-C3

The `sysbuild/mcuboot/boards/esp32c3_devkitm.overlay` exists but ESP32-C3
remains on the non-sysbuild list. Espressif chips use a different bootloader
chain (ROM bootloader → second-stage) that needs investigation before
enabling MCUboot.

## Test firmware and scripts

### GPIO test firmware (`scripts/gpio_test_fw/`)

Standalone Zephyr app for validating GPIO wiring. Shell commands:
- `gpio set <pin> <0|1>` — configure pin as output, set value
- `gpio get <pin>` — configure pin as input, read value
- `gpio all_out <0|1>` — set all testable pins
- `gpio all_in` — read all testable pins

Build: `west build -p always -b esp32c3_devkitm scripts/gpio_test_fw`

### UART test firmware (`scripts/uart_test_fw/`)

Standalone Zephyr app for validating UART1 wiring. Configures UART1
(TX=GPIO10, RX=GPIO3) as an echo server. Shell commands:
- `uart start/stop` — enable/disable echo
- `uart send <text>` — send text out UART1
- `uart status` — show byte counters

Build: `west build -p always -b esp32c3_devkitm scripts/uart_test_fw`

### Validation scripts (run on rpi4-esp)

- **`scripts/validate_gpio_wiring.py`** — For each wired GPIO: toggle ESP pin
  via serial, verify RPi reads correct value via gpiod, check no other GPIOs
  changed. Requires `python3-gpiod` on RPi.

- **`scripts/validate_uart.py`** — Sends test patterns via RPi UART, verifies
  echo from ESP32-C3 UART1. Tests both directions.

- **`scripts/esp32c3_serial_test.py`** — Sends shell commands to ESP32-C3 via
  USB serial and captures responses. Basic serial interaction tool.

- **`scripts/start_test_ap.sh`** — Sets up hostapd + dnsmasq on wlanE as an
  open WiFi AP named "esp-test" for ESP32-C3 WiFi testing.

- **`scripts/test_provision_api.py`** — POSTs WiFi credentials to the
  provisioning API endpoint.

## What needs to be done next

### High priority

1. **Fix WiFi event callback** — investigate why `NET_EVENT_WIFI_CONNECT_RESULT`
   doesn't fire on ESP32 WiFi driver. This blocks reliable auto-reconnect.

2. **Fix boot-time DHCP** — ensure WiFi auto-connect at boot reliably gets a
   DHCP lease without manual intervention.

3. **Add SoftAP DHCP server** — so phone/laptop users can complete WiFi
   provisioning without manual IP configuration.

### Medium priority

4. **Console bridge hardware test** — the UART wiring is validated, but the
   console bridge (TCP port 22 ↔ UART1) hasn't been tested end-to-end with
   WallaBMC firmware over WiFi. Requires WiFi to work reliably first.

5. **JTAG hardware test** — JTAG GPIO pins are wired (GPIO4-7 ↔ RPi GPIO12,
   18, 23, 24) but the JTAG daemon hasn't been tested. Needs a target device
   connected to the JTAG pins.

6. **Automated CI test script** — write `scripts/run_esp32c3_ci.py` similar to
   `run_qemu_ci.py` that flashes, boots, runs shell commands via serial, and
   optionally tests WiFi. Could run on rpi4-esp as a self-hosted runner.

### Low priority

7. **MCUboot for ESP32-C3** — investigate Espressif bootloader chain, validate
   sysbuild, enable OTA updates.

8. **HTTPS** — currently disabled (`CONFIG_APP_HTTPS=n`) due to RAM constraints.
   Profile RAM usage under WiFi+Web+Redfish load to determine feasibility.

9. **Zephyr ESP32-C3 QEMU target** — would enable runtime testing in CI without
   hardware. Requires creating a custom Zephyr board definition for Espressif's
   QEMU fork. Significant effort.

## File inventory

### New files

| File | Purpose |
|------|---------|
| `boards/esp32c3_devkitm.overlay` | ESP32-C3 device tree overlay |
| `boards/esp32c3_devkitm.conf` | ESP32-C3 board config |
| `src/wifi.c` | WiFi connection management |
| `src/wifi.h` | WiFi module header |
| `src/wifi_provision.c` | SoftAP provisioning + HTTP handlers |
| `src/wifi_provision.h` | WiFi provisioning header |
| `static_web_resources/provision.html` | Provisioning web page |
| `sysbuild/mcuboot/boards/esp32c3_devkitm.overlay` | MCUboot overlay (not yet enabled) |
| `docs/hardware/rpi-esp32c3.md` | Hardware test rig documentation |
| `docs/esp32c3-port-status.md` | This document |
| `scripts/gpio_test_fw/` | GPIO validation firmware (4 files) |
| `scripts/uart_test_fw/` | UART validation firmware (4 files) |
| `scripts/validate_gpio_wiring.py` | RPi-side GPIO validation script |
| `scripts/validate_uart.py` | RPi-side UART validation script |
| `scripts/esp32c3_serial_test.py` | ESP32-C3 serial interaction tool |
| `scripts/esp32c3_wifi_debug.py` | WiFi debug script (scan + connect with long capture) |
| `scripts/start_test_ap.sh` | Test WiFi AP setup script |
| `scripts/setup_test_ap.sh` | Alternative AP setup script |
| `scripts/test_provision_api.py` | Provisioning API test script |
| `scripts/check_gpiod.py` | gpiod availability check |
| `scripts/check_gpiod_api.py` | gpiod API probe script |

### Modified files

| File | Change |
|------|--------|
| `CMakeLists.txt` | Non-sysbuild list, WiFi source, provision HTML, conditional SRAM relocation |
| `Kconfig` | `APP_WIFI`, `APP_WIFI_PROVISION`, conditional `CODE_DATA_RELOCATION_SRAM` |
| `src/main.c` | WiFi + provisioning init in boot sequence |
| `src/config.c` | WiFi config entries (IDs 11-13), shell commands |
| `src/config.h` | WiFi config accessor declarations |
| `src/console_bridge.c` | `DT_ALIAS(console_bridge_uart)` portability |
| `src/rtc.h` | Fixed stub function signature |
| `boards/hifive_premier_p550_mcu.overlay` | Added `console-bridge-uart` alias |
| `boards/nucleo_f767zi.overlay` | Added `console-bridge-uart` alias |
| `.github/workflows/build.yml` | ESP32-C3 in build matrix |
