#ifndef __USB_IDENTITY_H
#define __USB_IDENTITY_H

struct usb_identity_pin_s {
    int pin;
    uint8_t invert;
    uint8_t pullup;
};

// out/compile_time_request.c (auto generated file)
extern const struct usb_identity_pin_s usb_identity_pin;

// functions
struct usb_string_descriptor* usbserial_get_identity(void);

#endif // usb_identity.h
