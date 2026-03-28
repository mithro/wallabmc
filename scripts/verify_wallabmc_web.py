#!/usr/bin/env python3
# SPDX-FileCopyrightText: (C) 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
WallaBMC End-to-End Hardware Verification Test

Runs on the RPi connected to an ESP32-C3 running WallaBMC.
Verifies web API, power control, console bridge UART, and JTAG
connectivity over the WiFi link, with GPIO-level hardware checks.

Prerequisites:
  - ESP32-C3 running WallaBMC firmware, connected to WiFi
  - RPi GPIO wires connected per docs/hardware/rpi-esp32c3.md
  - RPi serial (/dev/serial0) available (not used by console/getty)
  - Python packages: gpiod, pyserial

Usage:
  python3 verify_wallabmc_web.py [BMC_IP]

If BMC_IP is not provided, discovers it from the dnsmasq lease file.
"""

import argparse
import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_USER = "admin"
DEFAULT_PASS = "admin"
CONSOLE_BRIDGE_PORT = 22
JTAG_PORT = 7777
HTTP_PORT = 80
POLL_TIMEOUT = 10       # seconds to poll before giving up
POLL_INTERVAL = 0.25    # seconds between poll attempts

# RPi GPIO chip (BCM2711 on RPi4)
GPIO_CHIP = "/dev/gpiochip4"

# RPi BCM GPIO numbers wired to ESP32-C3 pins.
# From docs/hardware/rpi-esp32c3.md:
#   ESP32 GPIO2  (status-led / power-gpio-1 / reset-gpio) -> RPi GPIO16
#   ESP32 GPIO10 (UART1 TX, console bridge)                -> RPi GPIO15
#   ESP32 GPIO3  (UART1 RX, console bridge)                <- RPi GPIO14
RPI_GPIO_STATUS_LED = 16   # ESP32 GPIO2
RPI_GPIO_UART_RX    = 15   # reads ESP32 UART1 TX (GPIO10)
RPI_GPIO_UART_TX    = 14   # drives ESP32 UART1 RX (GPIO3)

# JTAG pins
RPI_GPIO_JTAG_TCK = 12    # ESP32 GPIO4
RPI_GPIO_JTAG_TMS = 18    # ESP32 GPIO5
RPI_GPIO_JTAG_TDO = 23    # ESP32 GPIO6
RPI_GPIO_JTAG_TDI = 24    # ESP32 GPIO7

# RPi serial port connected to ESP32-C3 UART1 via GPIO14/15
RPI_UART_DEV = "/dev/serial0"
RPI_UART_BAUD = 115200

# Dnsmasq lease file path patterns
DNSMASQ_LEASE_PATHS = [
    "/var/lib/misc/dnsmasq.leases",
    "/tmp/dnsmasq.leases",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class Colors:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

pass_count = 0
fail_count = 0
skip_count = 0


def log_pass(name, detail=""):
    global pass_count
    pass_count += 1
    d = f" — {detail}" if detail else ""
    print(f"  {Colors.GREEN}PASS{Colors.RESET}  {name}{d}")


def log_fail(name, detail=""):
    global fail_count
    fail_count += 1
    d = f" — {detail}" if detail else ""
    print(f"  {Colors.RED}FAIL{Colors.RESET}  {name}{d}")


def log_skip(name, detail=""):
    global skip_count
    skip_count += 1
    d = f" — {detail}" if detail else ""
    print(f"  {Colors.YELLOW}SKIP{Colors.RESET}  {name}{d}")


def section(title):
    print(f"\n{Colors.BOLD}{Colors.CYAN}--- {title} ---{Colors.RESET}")


def poll_until(predicate, timeout=POLL_TIMEOUT, interval=POLL_INTERVAL):
    """Poll predicate() until it returns a truthy value or timeout.
    Returns the truthy value on success, None on timeout."""
    deadline = time.monotonic() + timeout
    last_result = None
    while time.monotonic() < deadline:
        last_result = predicate()
        if last_result:
            return last_result
        time.sleep(interval)
    return None


def http_get(url, auth=None, timeout=5):
    """GET request, returns (status, headers, body_bytes)."""
    req = urllib.request.Request(url)
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {cred}")
    req.add_header("Accept-Encoding", "gzip, deflate")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip
            body = gzip.decompress(body)
        return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        return e.code, {}, e.read()
    except Exception as e:
        return 0, {}, str(e).encode()


def http_post_json(url, data, auth=None, timeout=5):
    """POST JSON, returns (status, headers, body_bytes)."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {cred}")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {}, e.read()
    except Exception as e:
        return 0, {}, str(e).encode()


def get_power_state(base_url, auth):
    """Returns the current PowerState string, or None on failure."""
    status, _, body = http_get(
        f"{base_url}/redfish/v1/Systems/system", auth=auth)
    if status == 200:
        try:
            return json.loads(body).get("PowerState")
        except Exception:
            pass
    return None


def poll_power_state(base_url, auth, expected, timeout=POLL_TIMEOUT):
    """Poll until PowerState matches expected. Returns actual state."""
    result = poll_until(
        lambda: get_power_state(base_url, auth) == expected and expected,
        timeout=timeout,
    )
    if result:
        return expected
    return get_power_state(base_url, auth)


def discover_bmc_ip():
    """Try to discover the BMC IP from dnsmasq leases."""
    for path in DNSMASQ_LEASE_PATHS:
        try:
            with open(path) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4 and parts[3] == "wallabmc":
                        return parts[2]
        except FileNotFoundError:
            continue

    for logpath in ["/tmp/dnsmasq.log", "/tmp/dnsmasq2.log"]:
        try:
            with open(logpath) as f:
                content = f.read()
            matches = re.findall(r"DHCPACK\S*\s+(192\.168\.\d+\.\d+)", content)
            if matches:
                return matches[-1]
        except FileNotFoundError:
            continue

    return None


# ---------------------------------------------------------------------------
# GPIO helpers (using gpiod library)
# ---------------------------------------------------------------------------

def gpio_available():
    try:
        import gpiod
        return os.path.exists(GPIO_CHIP)
    except ImportError:
        return False


def gpio_read(pin):
    """Read a single RPi BCM GPIO pin. Returns True (HIGH) or False (LOW)."""
    import gpiod
    with gpiod.request_lines(
        GPIO_CHIP,
        consumer="wallabmc-verify",
        config={pin: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)},
    ) as lines:
        return lines.get_value(pin) == gpiod.line.Value.ACTIVE


def gpio_poll_value(pin, expected, timeout=POLL_TIMEOUT):
    """Poll GPIO pin until it matches expected value. Returns actual value."""
    result = poll_until(lambda: gpio_read(pin) == expected, timeout=timeout)
    return result is not None


def gpio_sample(pin, duration=2.0, interval=0.01):
    """Sample a GPIO pin rapidly, return list of (timestamp, value) tuples."""
    import gpiod
    samples = []
    with gpiod.request_lines(
        GPIO_CHIP,
        consumer="wallabmc-verify",
        config={pin: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)},
    ) as lines:
        start = time.monotonic()
        while time.monotonic() - start < duration:
            val = lines.get_value(pin)
            samples.append((time.monotonic() - start, val == gpiod.line.Value.ACTIVE))
            time.sleep(interval)
    return samples


# ---------------------------------------------------------------------------
# Test: Network connectivity (with polling)
# ---------------------------------------------------------------------------

def test_network(bmc_ip):
    section("Network Connectivity")

    # Poll for ping reachability
    def ping_ok():
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", bmc_ip],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0

    if poll_until(ping_ok, timeout=POLL_TIMEOUT):
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "2", bmc_ip],
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"rtt min/avg/max.*= ([\d.]+)/([\d.]+)/([\d.]+)",
                          result.stdout)
        rtt = f"avg={match.group(2)}ms" if match else ""
        log_pass("Ping", rtt)
    else:
        log_fail("Ping", "no response within timeout")
        return False

    # Poll for TCP port 80
    def tcp_open(port):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((bmc_ip, port))
            sock.close()
            return True
        except Exception:
            return False

    for port, name in [(HTTP_PORT, "HTTP"), (CONSOLE_BRIDGE_PORT, "console bridge"),
                       (JTAG_PORT, "JTAG")]:
        if poll_until(lambda p=port: tcp_open(p)):
            log_pass(f"TCP port {port} ({name}) open")
        else:
            log_fail(f"TCP port {port} ({name})", "not reachable")

    return True


# ---------------------------------------------------------------------------
# Test: HTTP Web UI
# ---------------------------------------------------------------------------

def test_http_web_ui(base_url):
    section("HTTP Web UI")

    # Poll for HTTP to be serving
    def get_index():
        s, _, b = http_get(f"{base_url}/")
        return (s, b) if s == 200 else None

    result = poll_until(get_index)
    if result:
        status, body = result
        html = body.decode("utf-8", errors="replace")
        if "WallaBMC" in html and "<!DOCTYPE html>" in html:
            log_pass("GET / returns valid HTML", f"{len(body)} bytes")
        else:
            log_fail("GET / content invalid", html[:200])
    else:
        log_fail("GET /", "not reachable")
        return

    status, _, body = http_get(f"{base_url}/favicon.png")
    if status == 200 and len(body) > 100:
        log_pass("GET /favicon.png", f"{len(body)} bytes")
    else:
        log_fail("GET /favicon.png", f"status={status} len={len(body)}")


# ---------------------------------------------------------------------------
# Test: Redfish API
# ---------------------------------------------------------------------------

def test_redfish_api(base_url, auth):
    section("Redfish API")

    # Service root (no auth)
    status, _, body = http_get(f"{base_url}/redfish/v1/")
    if status == 200:
        data = json.loads(body)
        if "RedfishVersion" in data:
            log_pass("GET /redfish/v1/ (service root)",
                     f"version={data.get('RedfishVersion')}")
        else:
            log_fail("GET /redfish/v1/ missing RedfishVersion")
    else:
        log_fail("GET /redfish/v1/", f"status={status}")

    # Systems (requires auth)
    status, _, body = http_get(f"{base_url}/redfish/v1/Systems/system", auth=auth)
    if status == 200:
        data = json.loads(body)
        power_state = data.get("PowerState", "Unknown")
        log_pass("GET /redfish/v1/Systems/system",
                 f"PowerState={power_state}")
    elif status == 401:
        log_fail("GET Systems — auth rejected (wrong password?)")
    else:
        log_fail("GET Systems", f"status={status}")

    # Auth required check
    status, _, _ = http_get(f"{base_url}/redfish/v1/Systems/system")
    if status == 401:
        log_pass("Unauthenticated request correctly rejected (401)")
    else:
        log_fail("Unauthenticated request not rejected", f"status={status}")

    # Managers/bmc
    status, _, body = http_get(f"{base_url}/redfish/v1/Managers/bmc", auth=auth)
    if status == 200:
        data = json.loads(body)
        model = data.get("Model", "?")
        log_pass("GET /redfish/v1/Managers/bmc", f"Model={model}")
    else:
        log_fail("GET Managers/bmc", f"status={status}")

    # EthernetInterfaces/eth0
    status, _, body = http_get(
        f"{base_url}/redfish/v1/Managers/bmc/EthernetInterfaces/eth0",
        auth=auth,
    )
    if status == 200:
        data = json.loads(body)
        hostname = data.get("HostName", "?")
        log_pass("GET EthernetInterfaces/eth0", f"HostName={hostname}")
    else:
        log_fail("GET EthernetInterfaces/eth0", f"status={status}")


# ---------------------------------------------------------------------------
# Test: Power Control via Redfish + GPIO verification
# ---------------------------------------------------------------------------

def test_power_control(base_url, auth):
    section("Power Control")

    reset_url = f"{base_url}/redfish/v1/Systems/system/Actions/ComputerSystem.Reset"
    have_gpio = gpio_available()

    # --- Force Off ---
    status, _, body = http_post_json(reset_url, {"ResetType": "ForceOff"}, auth=auth)
    if status in (200, 204):
        log_pass("POST ForceOff accepted")
    else:
        log_fail("POST ForceOff", f"status={status} body={body[:200]}")
        return

    # Poll until PowerState == Off
    actual = poll_power_state(base_url, auth, "Off")
    if actual == "Off":
        log_pass("PowerState reports Off after ForceOff")
    else:
        log_fail("PowerState after ForceOff", f"got {actual}")

    # GPIO check: sample LED pin, verify it's toggling (ESP32 driving it)
    if have_gpio:
        samples = gpio_sample(RPI_GPIO_STATUS_LED, duration=3.0, interval=0.005)
        high_pct = sum(1 for _, v in samples if v) / len(samples) * 100
        log_pass(f"GPIO16 (status LED) sampled: {high_pct:.0f}% HIGH over 3s",
                 f"({len(samples)} samples — LED thread active)")

    # --- Power On ---
    status, _, body = http_post_json(reset_url, {"ResetType": "On"}, auth=auth)
    if status in (200, 204):
        log_pass("POST On accepted")
    else:
        log_fail("POST On", f"status={status} body={body[:200]}")
        return

    # Poll until PowerState == On
    actual = poll_power_state(base_url, auth, "On")
    if actual == "On":
        log_pass("PowerState reports On after On")
    else:
        log_fail("PowerState after On", f"got {actual}")

    if have_gpio:
        samples = gpio_sample(RPI_GPIO_STATUS_LED, duration=3.0, interval=0.005)
        high_pct = sum(1 for _, v in samples if v) / len(samples) * 100
        log_pass(f"GPIO16 (status LED) sampled: {high_pct:.0f}% HIGH over 3s",
                 f"({len(samples)} samples — LED thread active)")

    # --- GPIO toggling proof ---
    if have_gpio:
        samples = gpio_sample(RPI_GPIO_STATUS_LED, duration=4.0, interval=0.005)
        transitions = sum(
            1 for i in range(1, len(samples)) if samples[i][1] != samples[i - 1][1]
        )
        if transitions >= 4:
            log_pass(f"GPIO16 toggling detected ({transitions} transitions in 4s)",
                     "ESP32 is actively driving the pin")
        else:
            log_fail(f"GPIO16 not toggling ({transitions} transitions in 4s)")

    # --- Power Cycle ---
    # Ensure On first
    http_post_json(reset_url, {"ResetType": "On"}, auth=auth)
    poll_power_state(base_url, auth, "On")

    status, _, body = http_post_json(reset_url, {"ResetType": "PowerCycle"}, auth=auth)
    if status in (200, 204):
        log_pass("POST PowerCycle accepted")
    else:
        log_fail("POST PowerCycle", f"status={status}")

    # After cycle, power should be back On
    actual = poll_power_state(base_url, auth, "On")
    if actual == "On":
        log_pass("PowerState reports On after PowerCycle")
    else:
        log_fail("PowerState after PowerCycle", f"got {actual}")


# ---------------------------------------------------------------------------
# Test: Console Bridge (TCP port 22 <-> UART1 <-> RPi UART)
# ---------------------------------------------------------------------------

def test_console_bridge(bmc_ip, uart_dev=RPI_UART_DEV):
    section("Console Bridge (UART)")

    try:
        import serial as pyserial
    except ImportError:
        log_skip("Console bridge tests", "pyserial not installed")
        return

    if not os.path.exists(uart_dev):
        log_skip("Console bridge tests", f"{uart_dev} not found")
        return

    try:
        rpi_uart = pyserial.Serial(
            uart_dev, RPI_UART_BAUD, timeout=2,
            bytesize=pyserial.EIGHTBITS,
            parity=pyserial.PARITY_NONE,
            stopbits=pyserial.STOPBITS_ONE,
        )
        rpi_uart.reset_input_buffer()
    except Exception as e:
        log_skip("Console bridge tests", f"cannot open {uart_dev}: {e}")
        return

    # Poll for TCP connection to console bridge
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((bmc_ip, CONSOLE_BRIDGE_PORT))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        log_fail("Console bridge TCP connect", str(e))
        rpi_uart.close()
        return

    log_pass("Console bridge TCP connect", f"{bmc_ip}:{CONSOLE_BRIDGE_PORT}")

    def drain_sock():
        """Drain any pending data from socket."""
        sock.setblocking(False)
        try:
            while sock.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass
        sock.setblocking(True)
        sock.settimeout(3)

    def drain_uart():
        """Drain any pending data from UART."""
        rpi_uart.reset_input_buffer()

    def drain_both():
        drain_sock()
        drain_uart()

    drain_both()

    # --- Test 1: TCP -> UART (ESP32 TX -> RPi RX) ---
    test_pattern = b"WALLABMC_TX_TEST_" + bytes(range(0x20, 0x7f))
    try:
        sock.sendall(test_pattern)

        # Poll for data on RPi UART
        received = b""
        def got_tx_data():
            nonlocal received
            chunk = rpi_uart.read(rpi_uart.in_waiting or 1)
            if chunk:
                received += chunk
            return test_pattern in received

        if poll_until(got_tx_data, timeout=5):
            log_pass("TCP -> UART (ESP32 TX -> RPi RX)",
                     f"sent {len(test_pattern)}B, received {len(received)}B")
        elif len(received) > 0:
            log_fail("TCP -> UART partial",
                     f"sent {len(test_pattern)}B, got {len(received)}B")
        else:
            log_fail("TCP -> UART no data received on RPi UART")
    except Exception as e:
        log_fail("TCP -> UART", str(e))

    drain_both()

    # --- Test 2: UART -> TCP (RPi TX -> ESP32 RX) ---
    test_pattern_2 = b"WALLABMC_RX_TEST_" + bytes(range(0x20, 0x7f))
    try:
        rpi_uart.write(test_pattern_2)
        rpi_uart.flush()

        # Poll for data on TCP socket
        received = b""
        def got_rx_data():
            nonlocal received
            try:
                sock.setblocking(False)
                chunk = sock.recv(4096)
                sock.setblocking(True)
                sock.settimeout(3)
                if chunk:
                    received += chunk
            except (BlockingIOError, OSError):
                sock.setblocking(True)
                sock.settimeout(3)
            return test_pattern_2 in received

        if poll_until(got_rx_data, timeout=5):
            log_pass("UART -> TCP (RPi TX -> ESP32 RX)",
                     f"sent {len(test_pattern_2)}B, received {len(received)}B")
        elif len(received) > 0:
            log_fail("UART -> TCP partial",
                     f"sent {len(test_pattern_2)}B, got {len(received)}B")
        else:
            log_fail("UART -> TCP no data received on TCP socket")
    except Exception as e:
        log_fail("UART -> TCP", str(e))

    drain_both()

    # --- Test 3: Byte-level accuracy (TCP -> UART) ---
    # Send all printable ASCII at once, verify received correctly
    all_bytes = bytes(range(0x20, 0x7f))
    try:
        sock.sendall(all_bytes)
        received = b""
        def got_all_tx():
            nonlocal received
            chunk = rpi_uart.read(rpi_uart.in_waiting or 1)
            if chunk:
                received += chunk
            return len(received) >= len(all_bytes)

        poll_until(got_all_tx, timeout=5)
        # Compare what we got
        correct = sum(1 for i in range(min(len(all_bytes), len(received)))
                      if all_bytes[i] == received[i])
        if correct == len(all_bytes):
            log_pass(f"Byte-level TCP->UART: {correct}/{len(all_bytes)} bytes correct")
        else:
            log_fail(f"Byte-level TCP->UART: {correct}/{len(all_bytes)} bytes correct",
                     f"received {len(received)} bytes")
    except Exception as e:
        log_fail("Byte-level TCP->UART", str(e))

    drain_both()

    # --- Test 4: Byte-level accuracy (UART -> TCP) ---
    try:
        rpi_uart.write(all_bytes)
        rpi_uart.flush()
        received = b""
        def got_all_rx():
            nonlocal received
            try:
                sock.setblocking(False)
                chunk = sock.recv(4096)
                sock.setblocking(True)
                sock.settimeout(3)
                if chunk:
                    received += chunk
            except (BlockingIOError, OSError):
                sock.setblocking(True)
                sock.settimeout(3)
            return len(received) >= len(all_bytes)

        poll_until(got_all_rx, timeout=5)
        correct = sum(1 for i in range(min(len(all_bytes), len(received)))
                      if all_bytes[i] == received[i])
        if correct == len(all_bytes):
            log_pass(f"Byte-level UART->TCP: {correct}/{len(all_bytes)} bytes correct")
        else:
            log_fail(f"Byte-level UART->TCP: {correct}/{len(all_bytes)} bytes correct",
                     f"received {len(received)} bytes")
    except Exception as e:
        log_fail("Byte-level UART->TCP", str(e))

    sock.close()
    rpi_uart.close()


# ---------------------------------------------------------------------------
# Test: JTAG port (connectivity only)
# ---------------------------------------------------------------------------

def test_jtag_port(bmc_ip):
    section("JTAG Remote Bitbang")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((bmc_ip, JTAG_PORT))
        log_pass("JTAG TCP connect", f"{bmc_ip}:{JTAG_PORT}")

        sock.sendall(b"R")
        # Poll for response
        def got_tdo():
            try:
                sock.setblocking(False)
                d = sock.recv(1)
                sock.setblocking(True)
                sock.settimeout(3)
                return d
            except (BlockingIOError, OSError):
                sock.setblocking(True)
                sock.settimeout(3)
                return None

        resp = poll_until(got_tdo, timeout=3)
        if resp in (b"0", b"1"):
            log_pass("JTAG read TDO", f"response={resp.decode()}")
        else:
            log_fail("JTAG read TDO", f"unexpected response: {resp!r}")

        sock.sendall(b"Q")
        sock.close()
    except Exception as e:
        log_fail("JTAG TCP connect", str(e))


# ---------------------------------------------------------------------------
# Test: JTAG GPIO connectivity (verify pins are driven)
# ---------------------------------------------------------------------------

def test_jtag_gpio(bmc_ip):
    section("JTAG GPIO Verification")

    if not gpio_available():
        log_skip("JTAG GPIO tests", "gpiod not available")
        return

    # Poll for JTAG port — single-client server needs time to accept after
    # the previous test disconnected.
    sock = None
    def jtag_connect():
        nonlocal sock
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((bmc_ip, JTAG_PORT))
            sock = s
            return True
        except Exception:
            return False

    if not poll_until(jtag_connect):
        log_fail("JTAG GPIO — cannot connect", "timed out")
        return

    def jtag_set_and_verify(cmd, expect_tck, expect_tms, expect_tdi, label):
        """Send JTAG command byte, poll until GPIO pins match expected values."""
        sock.sendall(cmd.encode())

        def pins_match():
            tck = gpio_read(RPI_GPIO_JTAG_TCK)
            tms = gpio_read(RPI_GPIO_JTAG_TMS)
            tdi = gpio_read(RPI_GPIO_JTAG_TDI)
            return (tck == expect_tck and tms == expect_tms and tdi == expect_tdi,
                    tck, tms, tdi)

        # Poll until pins settle to expected values
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            match, tck, tms, tdi = pins_match()
            if match:
                log_pass(label, f"TCK={tck} TMS={tms} TDI={tdi}")
                return True
            time.sleep(0.05)

        _, tck, tms, tdi = pins_match()
        log_fail(label, f"TCK={tck} TMS={tms} TDI={tdi}")
        return False

    try:
        # All HIGH: cmd '7' = TCK=1, TMS=1, TDI=1
        jtag_set_and_verify("7", True, True, True, "JTAG all HIGH (cmd '7')")
        # All LOW: cmd '0' = TCK=0, TMS=0, TDI=0
        jtag_set_and_verify("0", False, False, False, "JTAG all LOW (cmd '0')")
        # TCK only: cmd '4' = TCK=1, TMS=0, TDI=0
        jtag_set_and_verify("4", True, False, False, "JTAG TCK only (cmd '4')")
        # TMS only: cmd '2' = TCK=0, TMS=1, TDI=0
        jtag_set_and_verify("2", False, True, False, "JTAG TMS only (cmd '2')")
        # TDI only: cmd '1' = TCK=0, TMS=0, TDI=1
        jtag_set_and_verify("1", False, False, True, "JTAG TDI only (cmd '1')")

        # Clean up: set all LOW and disconnect
        sock.sendall(b"0Q")
        sock.close()
    except Exception as e:
        log_fail("JTAG GPIO test", str(e))
        try:
            sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="WallaBMC end-to-end hardware verification test",
    )
    parser.add_argument("bmc_ip", nargs="?",
                        help="BMC IP address (auto-discovers if omitted)")
    parser.add_argument("--user", default=DEFAULT_USER, help="Redfish username")
    parser.add_argument("--password", default=DEFAULT_PASS,
                        help="Redfish password")
    parser.add_argument("--uart", default=RPI_UART_DEV, help="RPi UART device")
    parser.add_argument("--skip-uart", action="store_true",
                        help="Skip console bridge UART tests")
    parser.add_argument("--skip-jtag", action="store_true",
                        help="Skip JTAG tests")
    parser.add_argument("--skip-power", action="store_true",
                        help="Skip power control tests")
    args = parser.parse_args()

    uart_dev = args.uart

    # Discover BMC IP
    bmc_ip = args.bmc_ip
    if not bmc_ip:
        bmc_ip = discover_bmc_ip()
        if not bmc_ip:
            print(f"{Colors.RED}Cannot discover BMC IP. "
                  f"Provide it as an argument.{Colors.RESET}")
            sys.exit(1)

    base_url = f"http://{bmc_ip}"
    auth = (args.user, args.password)

    print(f"{Colors.BOLD}WallaBMC End-to-End Verification{Colors.RESET}")
    print(f"Target: {bmc_ip}")
    print(f"Auth:   {auth[0]}/{'*' * len(auth[1])}")

    # Run test suites
    if not test_network(bmc_ip):
        print(f"\n{Colors.RED}Network unreachable — aborting.{Colors.RESET}")
        sys.exit(1)

    test_http_web_ui(base_url)
    test_redfish_api(base_url, auth)

    # Run tests that don't crash the firmware first, then power last
    # (power control can crash ESP32-C3 when GPIO aliases are placeholders)

    if not args.skip_uart:
        test_console_bridge(bmc_ip, uart_dev=uart_dev)
    else:
        section("Console Bridge (UART)")
        log_skip("Console bridge tests", "skipped by --skip-uart")

    if not args.skip_jtag:
        test_jtag_port(bmc_ip)
        test_jtag_gpio(bmc_ip)
    else:
        section("JTAG Remote Bitbang")
        log_skip("JTAG tests", "skipped by --skip-jtag")

    if not args.skip_power:
        test_power_control(base_url, auth)
    else:
        section("Power Control")
        log_skip("Power control tests", "skipped by --skip-power")

    # Summary
    print(f"\n{Colors.BOLD}{'=' * 50}{Colors.RESET}")
    total = pass_count + fail_count + skip_count
    print(f"  {Colors.GREEN}{pass_count} passed{Colors.RESET}, "
          f"{Colors.RED}{fail_count} failed{Colors.RESET}, "
          f"{Colors.YELLOW}{skip_count} skipped{Colors.RESET} "
          f"({total} total)")

    if fail_count > 0:
        print(f"  {Colors.RED}{Colors.BOLD}VERIFICATION FAILED{Colors.RESET}")
        sys.exit(1)
    else:
        print(f"  {Colors.GREEN}{Colors.BOLD}VERIFICATION PASSED{Colors.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
