/*
 * SPDX-FileCopyrightText: © 2025-2026 Tenstorrent AI ULC
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * GPIO test firmware for ESP32-C3 wiring validation.
 *
 * Shell commands:
 *   gpio set <pin> <0|1>   - Configure pin as output and set value
 *   gpio get <pin>         - Configure pin as input and read value
 *   gpio all_out <0|1>     - Set all testable pins to output with value
 *   gpio all_in            - Set all testable pins to input and read
 */

#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/drivers/gpio.h>
#include <stdlib.h>

#define GPIO0_NODE DT_NODELABEL(gpio0)
static const struct device *gpio_dev = DEVICE_DT_GET(GPIO0_NODE);

/* Pins available on the ESP32-C3 Super Mini */
static const int testable_pins[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 21};
#define NUM_TESTABLE_PINS ARRAY_SIZE(testable_pins)

static bool pin_is_testable(int pin)
{
	for (int i = 0; i < NUM_TESTABLE_PINS; i++) {
		if (testable_pins[i] == pin) {
			return true;
		}
	}
	return false;
}

static int cmd_gpio_set(const struct shell *sh, size_t argc, char **argv)
{
	int pin = atoi(argv[1]);
	int val = atoi(argv[2]);

	if (!pin_is_testable(pin)) {
		shell_error(sh, "Pin %d is not testable", pin);
		return -EINVAL;
	}

	int ret = gpio_pin_configure(gpio_dev, pin, GPIO_OUTPUT);
	if (ret < 0) {
		shell_error(sh, "Failed to configure pin %d as output: %d", pin, ret);
		return ret;
	}

	ret = gpio_pin_set(gpio_dev, pin, val);
	if (ret < 0) {
		shell_error(sh, "Failed to set pin %d: %d", pin, ret);
		return ret;
	}

	shell_print(sh, "OK pin %d = %d", pin, val);
	return 0;
}

static int cmd_gpio_get(const struct shell *sh, size_t argc, char **argv)
{
	int pin = atoi(argv[1]);

	if (!pin_is_testable(pin)) {
		shell_error(sh, "Pin %d is not testable", pin);
		return -EINVAL;
	}

	int ret = gpio_pin_configure(gpio_dev, pin, GPIO_INPUT);
	if (ret < 0) {
		shell_error(sh, "Failed to configure pin %d as input: %d", pin, ret);
		return ret;
	}

	int val = gpio_pin_get(gpio_dev, pin);
	if (val < 0) {
		shell_error(sh, "Failed to read pin %d: %d", pin, val);
		return val;
	}

	shell_print(sh, "OK pin %d = %d", pin, val);
	return 0;
}

static int cmd_gpio_all_out(const struct shell *sh, size_t argc, char **argv)
{
	int val = atoi(argv[1]);
	int ret;

	for (int i = 0; i < NUM_TESTABLE_PINS; i++) {
		int pin = testable_pins[i];

		ret = gpio_pin_configure(gpio_dev, pin, GPIO_OUTPUT);
		if (ret < 0) {
			shell_error(sh, "Failed to configure pin %d: %d", pin, ret);
			continue;
		}
		ret = gpio_pin_set(gpio_dev, pin, val);
		if (ret < 0) {
			shell_error(sh, "Failed to set pin %d: %d", pin, ret);
			continue;
		}
	}

	shell_print(sh, "OK all pins = %d", val);
	return 0;
}

static int cmd_gpio_all_in(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	for (int i = 0; i < NUM_TESTABLE_PINS; i++) {
		int pin = testable_pins[i];
		int ret;

		ret = gpio_pin_configure(gpio_dev, pin, GPIO_INPUT);
		if (ret < 0) {
			shell_print(sh, "pin %d = ERR(%d)", pin, ret);
			continue;
		}

		int val = gpio_pin_get(gpio_dev, pin);
		if (val < 0) {
			shell_print(sh, "pin %d = ERR(%d)", pin, val);
			continue;
		}

		shell_print(sh, "pin %d = %d", pin, val);
	}

	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(sub_gpio_cmds,
	SHELL_CMD_ARG(set, NULL,
		"Set GPIO pin value\n"
		"Usage: gpio set <pin> <0|1>",
		cmd_gpio_set, 3, 0),
	SHELL_CMD_ARG(get, NULL,
		"Read GPIO pin value\n"
		"Usage: gpio get <pin>",
		cmd_gpio_get, 2, 0),
	SHELL_CMD_ARG(all_out, NULL,
		"Set all testable pins as output\n"
		"Usage: gpio all_out <0|1>",
		cmd_gpio_all_out, 2, 0),
	SHELL_CMD(all_in, NULL,
		"Read all testable pins as input",
		cmd_gpio_all_in),
	SHELL_SUBCMD_SET_END
);

SHELL_CMD_REGISTER(gpio, &sub_gpio_cmds, "GPIO test commands", NULL);

int main(void)
{
	if (!device_is_ready(gpio_dev)) {
		printk("GPIO device not ready!\n");
		return -1;
	}

	printk("\n");
	printk("=== ESP32-C3 GPIO Test Firmware ===\n");
	printk("Testable pins: 0,1,2,3,4,5,6,7,8,9,10,20,21\n");
	printk("Commands: gpio set/get/all_out/all_in\n");
	printk("===================================\n");

	return 0;
}
