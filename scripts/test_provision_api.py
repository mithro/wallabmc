#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Test the WiFi provisioning API endpoint.

Usage: python3 test_provision_api.py <ssid> [psk]

Posts credentials to http://192.168.4.1/api/provision
"""

import sys
import json
import urllib.request

ssid = sys.argv[1] if len(sys.argv) > 1 else "esp-test"
psk = sys.argv[2] if len(sys.argv) > 2 else ""

url = "http://192.168.4.1/api/provision"
data = json.dumps({"ssid": ssid, "psk": psk}).encode("utf-8")

print(f"POST {url}")
print(f"  ssid: {ssid}")
print(f"  psk: {'(empty)' if not psk else '***'}")

req = urllib.request.Request(url, data=data,
                             headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    body = resp.read().decode("utf-8")
    print(f"Response: {resp.status} {body}")
except Exception as e:
    print(f"Error: {e}")
