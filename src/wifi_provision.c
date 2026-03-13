/*
 * SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * WiFi provisioning via SoftAP captive portal.
 *
 * When no WiFi credentials are stored (or the reset button is held at boot),
 * the device starts a SoftAP named "WallaBMC-XXXX" (last 4 hex digits of MAC).
 * The existing HTTP server on port 80 serves a provisioning page. A POST to
 * /api/provision saves SSID+PSK to NVS and reboots into WiFi client mode.
 */

#include <zephyr/kernel.h>

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(wifi_provision, LOG_LEVEL_INF);

#include <zephyr/net/net_if.h>
#include <zephyr/net/wifi_mgmt.h>
#include <zephyr/net/http/server.h>
#include <zephyr/net/http/service.h>
#include <zephyr/data/json.h>
#include <string.h>
#include <stdio.h>

#include "wifi_provision.h"
#include "wifi.h"
#include "config.h"
#include "main.h"

#define SOFTAP_CHANNEL 6
#define PROVISION_REBOOT_DELAY_MS 2000

static bool provision_active;

/* Scan results storage */
#define MAX_SCAN_RESULTS 16

struct scan_entry {
	char ssid[33];
	int8_t rssi;
};

static struct scan_entry scan_results[MAX_SCAN_RESULTS];
static int scan_count;
static bool scan_in_progress;

static struct net_mgmt_event_callback scan_cb;

static void scan_result_handler(struct net_mgmt_event_callback *cb,
				uint32_t mgmt_event, struct net_if *iface)
{
	if (mgmt_event == NET_EVENT_WIFI_SCAN_RESULT) {
		const struct wifi_scan_result *entry =
			(const struct wifi_scan_result *)cb->info;

		if (scan_count < MAX_SCAN_RESULTS && entry->ssid_length > 0) {
			memcpy(scan_results[scan_count].ssid, entry->ssid,
			       entry->ssid_length);
			scan_results[scan_count].ssid[entry->ssid_length] = '\0';
			scan_results[scan_count].rssi = entry->rssi;
			scan_count++;
		}
	} else if (mgmt_event == NET_EVENT_WIFI_SCAN_DONE) {
		scan_in_progress = false;
		LOG_INF("WiFi scan complete: %d networks found", scan_count);
	}
}

bool wifi_provision_is_active(void)
{
	return provision_active;
}

/* Provisioning page (static, gzip-compressed via build system) */
static const uint8_t provision_html_gz[] = {
#include "provision.html.gz.inc"
};

static struct http_resource_detail_static provision_html_resource_detail = {
	.common = {
		.type = HTTP_RESOURCE_TYPE_STATIC,
		.bitmask_of_supported_http_methods = BIT(HTTP_GET),
		.content_encoding = "gzip",
		.content_type = "text/html",
	},
	.static_data = provision_html_gz,
	.static_data_len = sizeof(provision_html_gz),
};

HTTP_RESOURCE_DEFINE(provision_html_resource, http_service, "/provision",
		     &provision_html_resource_detail);

/* Reboot work - deferred to allow HTTP response to be sent */
static void provision_reboot_work_fn(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(provision_reboot_work, provision_reboot_work_fn);

static void provision_reboot_work_fn(struct k_work *work)
{
	ARG_UNUSED(work);
	LOG_INF("Rebooting after WiFi provisioning...");
	bmc_reboot();
}

/*
 * POST /api/provision — save WiFi credentials and reboot
 * Body: {"ssid":"...","psk":"..."}
 */
static uint8_t provision_post_buf[256];

static int provision_api_handler(struct http_client_ctx *client,
				 enum http_data_status status,
				 const struct http_request_ctx *request_ctx,
				 struct http_response_ctx *response_ctx,
				 void *user_data)
{
	static size_t accumulated;

	if (status == HTTP_SERVER_DATA_ABORTED) {
		accumulated = 0;
		return 0;
	}

	/* Accumulate body data */
	if (request_ctx->data_len > 0) {
		size_t to_copy = request_ctx->data_len;

		if (accumulated + to_copy >= sizeof(provision_post_buf)) {
			to_copy = sizeof(provision_post_buf) - accumulated - 1;
		}
		memcpy(provision_post_buf + accumulated, request_ctx->data, to_copy);
		accumulated += to_copy;
	}

	if (status == HTTP_SERVER_DATA_FINAL) {
		provision_post_buf[accumulated] = '\0';
		accumulated = 0;

		/* Minimal JSON parsing — look for "ssid" and "psk" values */
		char ssid[33] = {0};
		char psk[65] = {0};

		/* Use Zephyr's JSON parser */
		struct provision_request {
			const char *ssid;
			const char *psk;
		} req = {0};

		static const struct json_obj_descr req_descr[] = {
			JSON_OBJ_DESCR_PRIM(struct provision_request, ssid, JSON_TOK_STRING),
			JSON_OBJ_DESCR_PRIM(struct provision_request, psk, JSON_TOK_STRING),
		};

		int ret = json_obj_parse((char *)provision_post_buf,
					 strlen((char *)provision_post_buf),
					 req_descr, ARRAY_SIZE(req_descr),
					 &req);
		if (ret < 0 || !req.ssid || strlen(req.ssid) == 0) {
			static const char err_body[] = "Invalid request";

			response_ctx->body = (const uint8_t *)err_body;
			response_ctx->body_len = sizeof(err_body) - 1;
			response_ctx->final_chunk = true;
			response_ctx->status = 400;
			return 0;
		}

		/* Copy to local buffers (json_obj_parse points into provision_post_buf) */
		strncpy(ssid, req.ssid, sizeof(ssid) - 1);
		if (req.psk) {
			strncpy(psk, req.psk, sizeof(psk) - 1);
		}

		LOG_INF("Provisioning: SSID=%s", ssid);

		/* Save credentials */
		config_wifi_ssid_set(ssid);
		config_wifi_psk_set(psk);
		config_wifi_autoconnect_set(true);

		static const char ok_body[] = "{\"status\":\"ok\"}";

		response_ctx->body = (const uint8_t *)ok_body;
		response_ctx->body_len = sizeof(ok_body) - 1;
		response_ctx->final_chunk = true;
		response_ctx->status = 200;

		/* Schedule reboot after response is sent */
		k_work_reschedule(&provision_reboot_work,
				  K_MSEC(PROVISION_REBOOT_DELAY_MS));
	}

	return 0;
}

static struct http_resource_detail_dynamic provision_api_detail = {
	.common = {
		.type = HTTP_RESOURCE_TYPE_DYNAMIC,
		.bitmask_of_supported_http_methods = BIT(HTTP_POST),
	},
	.cb = provision_api_handler,
	.user_data = NULL,
};

HTTP_RESOURCE_DEFINE(provision_api_resource, http_service, "/api/provision",
		     &provision_api_detail);

/*
 * GET /api/provision/scan — return scan results as JSON
 */
static uint8_t scan_response_buf[1024];

static int provision_scan_handler(struct http_client_ctx *client,
				  enum http_data_status status,
				  const struct http_request_ctx *request_ctx,
				  struct http_response_ctx *response_ctx,
				  void *user_data)
{
	if (status == HTTP_SERVER_DATA_FINAL) {
		/* Trigger a new scan if not already running */
		if (!scan_in_progress) {
			struct net_if *iface = net_if_get_default();

			scan_count = 0;
			scan_in_progress = true;
			net_mgmt(NET_REQUEST_WIFI_SCAN, iface, NULL, 0);

			/* Wait briefly for results (best effort) */
			k_msleep(3000);
		}

		/* Build JSON response */
		int off = snprintf((char *)scan_response_buf,
				   sizeof(scan_response_buf),
				   "{\"networks\":[");

		for (int i = 0; i < scan_count && off < (int)sizeof(scan_response_buf) - 40; i++) {
			if (i > 0) {
				off += snprintf((char *)scan_response_buf + off,
						sizeof(scan_response_buf) - off, ",");
			}
			off += snprintf((char *)scan_response_buf + off,
					sizeof(scan_response_buf) - off,
					"{\"ssid\":\"%s\",\"rssi\":%d}",
					scan_results[i].ssid,
					scan_results[i].rssi);
		}

		off += snprintf((char *)scan_response_buf + off,
				sizeof(scan_response_buf) - off, "]}");

		response_ctx->body = scan_response_buf;
		response_ctx->body_len = off;
		response_ctx->final_chunk = true;
		response_ctx->status = 200;
	}

	return 0;
}

static struct http_resource_detail_dynamic provision_scan_detail = {
	.common = {
		.type = HTTP_RESOURCE_TYPE_DYNAMIC,
		.bitmask_of_supported_http_methods = BIT(HTTP_GET),
	},
	.cb = provision_scan_handler,
	.user_data = NULL,
};

HTTP_RESOURCE_DEFINE(provision_scan_resource, http_service, "/api/provision/scan",
		     &provision_scan_detail);

static int start_softap(void)
{
	struct net_if *iface = net_if_get_default();

	if (!iface) {
		LOG_ERR("No default network interface");
		return -ENODEV;
	}

	/* Build SSID from MAC suffix */
	char ssid[32];
	struct net_linkaddr *linkaddr = net_if_get_link_addr(iface);

	if (linkaddr && linkaddr->len >= 2) {
		snprintf(ssid, sizeof(ssid), "WallaBMC-%02X%02X",
			 linkaddr->addr[linkaddr->len - 2],
			 linkaddr->addr[linkaddr->len - 1]);
	} else {
		snprintf(ssid, sizeof(ssid), "WallaBMC-Setup");
	}

	struct wifi_connect_req_params params = {0};

	params.ssid = (uint8_t *)ssid;
	params.ssid_length = strlen(ssid);
	params.channel = SOFTAP_CHANNEL;
	params.security = WIFI_SECURITY_TYPE_NONE;

	LOG_INF("Starting SoftAP: %s (channel %d)", ssid, SOFTAP_CHANNEL);

	int ret = net_mgmt(NET_REQUEST_WIFI_AP_ENABLE, iface, &params,
			   sizeof(struct wifi_connect_req_params));
	if (ret < 0) {
		LOG_ERR("Failed to start SoftAP: %d", ret);
		return ret;
	}

	LOG_INF("SoftAP started. Connect and visit http://192.168.4.1/provision");

	return 0;
}

int wifi_provision_init(void)
{
	const char *ssid = config_wifi_ssid();

	/* Register scan event handler */
	net_mgmt_init_event_callback(&scan_cb, scan_result_handler,
				     NET_EVENT_WIFI_SCAN_RESULT |
				     NET_EVENT_WIFI_SCAN_DONE);
	net_mgmt_add_event_callback(&scan_cb);

	/*
	 * Enter provisioning mode if no SSID is configured.
	 * The user can also trigger this via shell: wifi_provision start
	 */
	if (!ssid || strlen(ssid) == 0) {
		LOG_INF("No WiFi credentials configured, entering provisioning mode");
		provision_active = true;
		return start_softap();
	}

	return 0;
}
