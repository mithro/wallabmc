/*
 * SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/kernel.h>

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(wifi, LOG_LEVEL_INF);

#include <zephyr/net/net_if.h>
#include <zephyr/net/net_event.h>
#include <zephyr/net/wifi_mgmt.h>
#include <zephyr/net/dhcpv4.h>
#include <zephyr/shell/shell.h>
#include <string.h>

#include "wifi.h"
#include "config.h"

#define WIFI_RECONNECT_DELAY_MS 5000

static struct net_mgmt_event_callback wifi_cb;
static struct net_mgmt_event_callback ipv4_cb;

static bool wifi_connected;
static bool wifi_connecting;

static void wifi_auto_reconnect_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(wifi_reconnect_work, wifi_auto_reconnect_work_handler);

int wifi_connect(const char *ssid, const char *psk)
{
	struct net_if *iface = net_if_get_default();

	if (!iface) {
		LOG_ERR("No default network interface");
		return -ENODEV;
	}

	struct wifi_connect_req_params params = {0};

	params.ssid = (uint8_t *)ssid;
	params.ssid_length = strlen(ssid);
	params.channel = WIFI_CHANNEL_ANY;
	params.security = WIFI_SECURITY_TYPE_NONE;
	params.mfp = WIFI_MFP_OPTIONAL;

	if (psk && strlen(psk) > 0) {
		params.psk = (uint8_t *)psk;
		params.psk_length = strlen(psk);
		params.security = WIFI_SECURITY_TYPE_PSK;
	}

	LOG_INF("Connecting to WiFi SSID: %s", ssid);
	wifi_connecting = true;

	int ret = net_mgmt(NET_REQUEST_WIFI_CONNECT, iface, &params,
			   sizeof(struct wifi_connect_req_params));
	if (ret < 0) {
		LOG_ERR("WiFi connect request failed: %d", ret);
		wifi_connecting = false;
		return ret;
	}

	return 0;
}

int wifi_disconnect(void)
{
	struct net_if *iface = net_if_get_default();

	if (!iface) {
		return -ENODEV;
	}

	/* Cancel any pending reconnect */
	k_work_cancel_delayable(&wifi_reconnect_work);

	int ret = net_mgmt(NET_REQUEST_WIFI_DISCONNECT, iface, NULL, 0);
	if (ret < 0) {
		LOG_ERR("WiFi disconnect request failed: %d", ret);
		return ret;
	}

	return 0;
}

static void wifi_auto_reconnect_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	const char *ssid = config_wifi_ssid();
	const char *psk = config_wifi_psk();

	if (!ssid || strlen(ssid) == 0) {
		LOG_WRN("No WiFi SSID configured, skipping reconnect");
		return;
	}

	if (!config_wifi_autoconnect()) {
		return;
	}

	LOG_INF("Attempting WiFi reconnect...");
	wifi_connect(ssid, psk);
}

static void wifi_event_handler(struct net_mgmt_event_callback *cb,
				uint64_t mgmt_event, struct net_if *iface)
{
	switch (mgmt_event) {
	case NET_EVENT_WIFI_CONNECT_RESULT: {
		const struct wifi_status *status =
			(const struct wifi_status *)cb->info;

		if (status && status->status == 0) {
			LOG_INF("WiFi connected");
			wifi_connected = true;
			wifi_connecting = false;

			/*
			 * Restart DHCP so a fresh DISCOVER goes out now that
			 * the WiFi link is up.  net_config_init_app() may have
			 * already started DHCP before association completed,
			 * leaving the client in a stale selecting state with
			 * an inflated back-off timer.  A restart resets the
			 * state machine and gets an address promptly.
			 */
			net_dhcpv4_restart(iface);
		} else {
			LOG_WRN("WiFi connect failed: %d",
				status ? status->status : -1);
			wifi_connected = false;
			wifi_connecting = false;

			/* Schedule reconnect attempt */
			if (config_wifi_autoconnect()) {
				k_work_reschedule(&wifi_reconnect_work,
						  K_MSEC(WIFI_RECONNECT_DELAY_MS));
			}
		}
		break;
	}
	case NET_EVENT_WIFI_DISCONNECT_RESULT: {
		if (wifi_connected) {
			LOG_INF("WiFi disconnected");
			wifi_connected = false;
			wifi_connecting = false;

			/*
			 * Stop DHCP on the now-dead WiFi interface so the
			 * client does not keep retransmitting into the void.
			 * DHCP will be restarted by the connect handler when
			 * WiFi reconnects.
			 */
			net_dhcpv4_stop(iface);

			/* Schedule reconnect attempt */
			if (config_wifi_autoconnect()) {
				k_work_reschedule(&wifi_reconnect_work,
						  K_MSEC(WIFI_RECONNECT_DELAY_MS));
			}
		}
		break;
	}
	default:
		break;
	}
}

static void ipv4_event_handler(struct net_mgmt_event_callback *cb,
				uint64_t mgmt_event, struct net_if *iface)
{
	switch (mgmt_event) {
	case NET_EVENT_IPV4_ADDR_ADD:
		LOG_INF("WiFi interface got IPv4 address");
		break;
	case NET_EVENT_IPV4_ADDR_DEL:
		LOG_INF("WiFi interface lost IPv4 address");
		break;
	default:
		break;
	}
}

/* Shell commands */

static int cmd_wifi_connect(const struct shell *sh, size_t argc, char **argv)
{
	const char *ssid;
	const char *psk = NULL;

	if (argc >= 2) {
		ssid = argv[1];
		if (argc >= 3) {
			psk = argv[2];
		}
	} else {
		/* Use stored credentials */
		ssid = config_wifi_ssid();
		psk = config_wifi_psk();
		if (!ssid || strlen(ssid) == 0) {
			shell_error(sh, "No SSID provided and none configured");
			return -EINVAL;
		}
	}

	int ret = wifi_connect(ssid, psk);
	if (ret < 0) {
		shell_error(sh, "WiFi connect failed: %d", ret);
		return ret;
	}

	shell_info(sh, "WiFi connect request sent for SSID: %s", ssid);
	return 0;
}

static int cmd_wifi_disconnect(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	int ret = wifi_disconnect();
	if (ret < 0) {
		shell_error(sh, "WiFi disconnect failed: %d", ret);
		return ret;
	}

	shell_info(sh, "WiFi disconnect request sent");
	return 0;
}

static bool wifi_is_connected(void)
{
	struct net_if *iface = net_if_get_default();

	if (!iface) {
		return false;
	}

	/* Check actual interface carrier state rather than relying
	 * solely on the event callback, which may not fire on all
	 * WiFi drivers.
	 */
	if (net_if_is_carrier_ok(iface) && net_if_is_up(iface)) {
		/* Also check if we have an IPv4 address */
		struct net_if_config *cfg = net_if_get_config(iface);

		if (cfg) {
			for (int i = 0; i < NET_IF_MAX_IPV4_ADDR; i++) {
				if (cfg->ip.ipv4->unicast[i].ipv4.is_used &&
				    cfg->ip.ipv4->unicast[i].ipv4.address.in_addr.s_addr != 0) {
					wifi_connected = true;
					wifi_connecting = false;
					return true;
				}
			}
		}

		/* Carrier up but no IP yet — still connecting */
		return false;
	}

	return false;
}

static int cmd_wifi_status(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	bool connected = wifi_is_connected();

	shell_print(sh, "WiFi status: %s",
		    connected ? "connected" :
		    (wifi_connecting ? "connecting" : "disconnected"));
	shell_print(sh, "Configured SSID: %s", config_wifi_ssid());
	shell_print(sh, "Auto-connect: %s",
		    config_wifi_autoconnect() ? "enabled" : "disabled");

	return 0;
}

static int cmd_wifi_scan(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	struct net_if *iface = net_if_get_default();
	if (!iface) {
		shell_error(sh, "No default network interface");
		return -ENODEV;
	}

	int ret = net_mgmt(NET_REQUEST_WIFI_SCAN, iface, NULL, 0);
	if (ret < 0) {
		shell_error(sh, "WiFi scan request failed: %d", ret);
		return ret;
	}

	shell_info(sh, "WiFi scan started (results will appear in log)");
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(sub_wifi_cmds,
	SHELL_CMD_ARG(connect, NULL,
		"Connect to WiFi\n"
		"Usage: wifi connect [<SSID> [<PSK>]]",
		cmd_wifi_connect, 1, 2),
	SHELL_CMD(disconnect, NULL, "Disconnect from WiFi", cmd_wifi_disconnect),
	SHELL_CMD(status, NULL, "Show WiFi status", cmd_wifi_status),
	SHELL_CMD(scan, NULL, "Scan for WiFi networks", cmd_wifi_scan),
	SHELL_SUBCMD_SET_END
);

SHELL_CMD_REGISTER(wifi, &sub_wifi_cmds, "WiFi commands", NULL);

int wifi_init(void)
{
	/* Register for WiFi management events */
	net_mgmt_init_event_callback(&wifi_cb, wifi_event_handler,
				     NET_EVENT_WIFI_CONNECT_RESULT |
				     NET_EVENT_WIFI_DISCONNECT_RESULT);
	net_mgmt_add_event_callback(&wifi_cb);

	/* Register for IPv4 events */
	net_mgmt_init_event_callback(&ipv4_cb, ipv4_event_handler,
				     NET_EVENT_IPV4_ADDR_ADD |
				     NET_EVENT_IPV4_ADDR_DEL);
	net_mgmt_add_event_callback(&ipv4_cb);

	/* Auto-connect if credentials are configured */
	const char *ssid = config_wifi_ssid();
	if (ssid && strlen(ssid) > 0 && config_wifi_autoconnect()) {
		LOG_INF("Auto-connecting to WiFi SSID: %s", ssid);
		wifi_connect(ssid, config_wifi_psk());
	}

	return 0;
}
