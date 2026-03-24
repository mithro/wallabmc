#!/bin/bash
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
# Start a test WiFi AP on wlanE for ESP32-C3 testing.
# Run on rpi4-esp with: sudo bash ~/start_test_ap.sh

set -e

CONFDIR="$HOME/wallabmc-test"
mkdir -p "$CONFDIR"

ip addr flush dev wlanE
ip addr add 192.168.4.1/24 dev wlanE
ip link set wlanE up

cat > "$CONFDIR/hostapd.conf" <<'EOF'
interface=wlanE
driver=nl80211
ssid=esp-test
hw_mode=g
channel=6
wmm_enabled=0
auth_algs=1
wpa=0
EOF

hostapd -B "$CONFDIR/hostapd.conf"
echo "hostapd started (SSID: esp-test)"

dnsmasq --interface=wlanE --bind-interfaces \
    --dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h \
    --log-dhcp --keep-in-foreground &
echo "dnsmasq started (PID $!)"
echo "AP_READY"
