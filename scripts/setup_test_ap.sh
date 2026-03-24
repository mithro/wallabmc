#!/bin/bash
# SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
# Set up a test WiFi AP on wlanE for ESP32-C3 testing.
# Run on rpi4-esp with sudo.

set -e

IFACE=wlanE
SSID=esp-test
CHANNEL=6
IP=192.168.4.1
SUBNET=192.168.4.0/24
DHCP_RANGE=192.168.4.10,192.168.4.50,12h

echo "=== Setting up test AP on $IFACE ==="

# Write hostapd config
cat > /tmp/hostapd-test.conf <<EOF
interface=$IFACE
driver=nl80211
ssid=$SSID
hw_mode=g
channel=$CHANNEL
wmm_enabled=0
auth_algs=1
wpa=0
EOF

# Bring up interface with static IP
ip addr flush dev "$IFACE"
ip addr add "$IP/24" dev "$IFACE"
ip link set "$IFACE" up

# Start hostapd
hostapd -B /tmp/hostapd-test.conf
echo "hostapd started (SSID: $SSID)"

# Start dnsmasq for DHCP
dnsmasq --interface="$IFACE" --bind-interfaces \
    --dhcp-range="$DHCP_RANGE" \
    --no-daemon --log-queries &
DNSMASQ_PID=$!
echo "dnsmasq started (PID: $DNSMASQ_PID, range: $DHCP_RANGE)"
echo "=== AP ready ==="

# Wait for ctrl-c
trap "kill $DNSMASQ_PID 2>/dev/null; killall hostapd 2>/dev/null; echo 'Cleaned up'" EXIT
wait $DNSMASQ_PID
