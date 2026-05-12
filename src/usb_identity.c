// Support setting a usb serial string based on an input Pin
//
// Copyright (C) 2026  Franklyn Tackitt (kalico@frank.af)
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "autoconf.h" // CONFIG_USB_SERIAL_NUMBER_IDENTIFY
#include "board/gpio.h" // gpio_in_setup, gpio_in_read
#include "board/pgm.h" // READP
#include "generic/usb_cdc.h" // usb_fill_string
#include "generic/usbstd.h" // usb_string_descriptor
#include "board/misc.h" // timer_read_time
#include "ctr.h" // DECL_CTR
#include "usb_identity.h" // usb_identity_pin
#include "sched.h" // DECL_INIT

#define CONCAT1(a, b) a ## b
#define CONCAT(a, b) CONCAT1(a, b)


DECL_CTR("DECL_USB_SERIAL_IDENTITY_PIN " __stringify(CONFIG_USB_SERIAL_NUMBER_IDENTIFY_PIN));

#define USB_STR_SERIAL_PRIMARY CONCAT(u,CONFIG_USB_SERIAL_NUMBER_IDENTIFY_PRIMARY)
#define USB_STR_SERIAL_SECONDARY CONCAT(u,CONFIG_USB_SERIAL_NUMBER_IDENTIFY_SECONDARY)


#define SIZE_cdc_string_serial_primary \
    (sizeof(cdc_string_serial_primary) + sizeof(USB_STR_SERIAL_PRIMARY) - 2)

static struct usb_string_descriptor cdc_string_serial_primary PROGMEM = {
    .bLength = SIZE_cdc_string_serial_primary,
    .bDescriptorType = USB_DT_STRING,
    .data = USB_STR_SERIAL_PRIMARY,
};


#define SIZE_cdc_string_serial_secondary \
    (sizeof(cdc_string_serial_secondary) + sizeof(USB_STR_SERIAL_SECONDARY) - 2)

static struct usb_string_descriptor cdc_string_serial_secondary PROGMEM = {
    .bLength = SIZE_cdc_string_serial_secondary,
    .bDescriptorType = USB_DT_STRING,
    .data = USB_STR_SERIAL_SECONDARY,
};


static struct usb_string_descriptor* usb_identity;

struct usb_string_descriptor *
usbserial_get_identity(void)
{
    return usb_identity;
}

void
usb_identity_init(void)
{
    if (!CONFIG_USB_SERIAL_NUMBER_IDENTIFY)
        return;

    struct gpio_in identity_pin = gpio_in_setup(usb_identity_pin.pin, usb_identity_pin.pullup);

    // wait 1ms
    uint32_t endtime = timer_read_time() + timer_from_us(1000);
    while (timer_is_before(timer_read_time(), endtime))
        ;

    if (gpio_in_read(identity_pin) == usb_identity_pin.invert) {
        usb_identity = &cdc_string_serial_primary;
    } else {
        usb_identity = &cdc_string_serial_secondary;
    }
}
DECL_INIT(usb_identity_init);