/*
 * SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __WIFI_PROVISION_H__
#define __WIFI_PROVISION_H__

#ifdef CONFIG_APP_WIFI_PROVISION
int wifi_provision_init(void);
bool wifi_provision_is_active(void);
#else
static inline int wifi_provision_init(void) { return 0; }
static inline bool wifi_provision_is_active(void) { return false; }
#endif

#endif
