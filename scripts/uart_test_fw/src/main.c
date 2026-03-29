/*
 * SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * UART echo test firmware for ESP32-C3.
 *
 * Configures UART1 (TX=GPIO10, RX=GPIO3) and echoes received bytes.
 * The USB serial console provides shell commands to control the echo:
 *
 *   uart start     - Start echoing (default at boot)
 *   uart stop      - Stop echoing
 *   uart send <text> - Send text out UART1 TX
 *   uart status    - Show byte counters
 */

#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/drivers/uart.h>
#include <string.h>

#define UART1_NODE DT_NODELABEL(uart1)
static const struct device *uart1_dev = DEVICE_DT_GET(UART1_NODE);

static volatile bool echo_enabled = true;
static volatile uint32_t rx_count;
static volatile uint32_t tx_count;

static void uart1_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);

	while (uart_irq_update(dev) && uart_irq_is_pending(dev)) {
		if (uart_irq_rx_ready(dev)) {
			uint8_t c;

			while (uart_fifo_read(dev, &c, 1) == 1) {
				rx_count++;
				if (echo_enabled) {
					uart_poll_out(dev, c);
					tx_count++;
				}
			}
		}
	}
}

static int cmd_uart_start(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	echo_enabled = true;
	shell_print(sh, "UART1 echo enabled");
	return 0;
}

static int cmd_uart_stop(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	echo_enabled = false;
	shell_print(sh, "UART1 echo disabled");
	return 0;
}

static int cmd_uart_send(const struct shell *sh, size_t argc, char **argv)
{
	const char *text = argv[1];

	for (size_t i = 0; i < strlen(text); i++) {
		uart_poll_out(uart1_dev, text[i]);
		tx_count++;
	}
	/* Send newline */
	uart_poll_out(uart1_dev, '\r');
	uart_poll_out(uart1_dev, '\n');
	tx_count += 2;

	shell_print(sh, "Sent %zu bytes on UART1", strlen(text) + 2);
	return 0;
}

static int cmd_uart_status(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_print(sh, "UART1 echo: %s", echo_enabled ? "enabled" : "disabled");
	shell_print(sh, "RX bytes: %u", rx_count);
	shell_print(sh, "TX bytes: %u", tx_count);
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(sub_uart_cmds,
	SHELL_CMD(start, NULL, "Enable UART1 echo", cmd_uart_start),
	SHELL_CMD(stop, NULL, "Disable UART1 echo", cmd_uart_stop),
	SHELL_CMD_ARG(send, NULL,
		"Send text on UART1\n"
		"Usage: uart send <text>",
		cmd_uart_send, 2, 0),
	SHELL_CMD(status, NULL, "Show UART1 counters", cmd_uart_status),
	SHELL_SUBCMD_SET_END
);

SHELL_CMD_REGISTER(uart, &sub_uart_cmds, "UART1 test commands", NULL);

int main(void)
{
	if (!device_is_ready(uart1_dev)) {
		printk("UART1 device not ready!\n");
		return -1;
	}

	/* Configure UART1 for interrupt-driven RX */
	uart_irq_callback_set(uart1_dev, uart1_isr);
	uart_irq_rx_enable(uart1_dev);

	printk("\n");
	printk("=== ESP32-C3 UART Echo Test Firmware ===\n");
	printk("UART1: TX=GPIO10, RX=GPIO3, 115200 baud\n");
	printk("Echo mode: enabled (received bytes are echoed back)\n");
	printk("Commands: uart start/stop/send/status\n");
	printk("=========================================\n");

	return 0;
}
