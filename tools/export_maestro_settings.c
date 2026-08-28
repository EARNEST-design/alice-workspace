// Read-only Mini Maestro 12 channel settings exporter.
// Uses Pololu's documented native USB GET_PARAMETER request (0x81).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct libusb_context libusb_context;
typedef struct libusb_device_handle libusb_device_handle;
int libusb_init(libusb_context **ctx);
void libusb_exit(libusb_context *ctx);
libusb_device_handle *libusb_open_device_with_vid_pid(
    libusb_context *ctx, uint16_t vendor_id, uint16_t product_id);
void libusb_close(libusb_device_handle *dev_handle);
int libusb_control_transfer(libusb_device_handle *dev_handle,
    uint8_t request_type, uint8_t request, uint16_t value, uint16_t index,
    unsigned char *data, uint16_t length, unsigned int timeout);

enum {
    POLOLU_VID = 0x1ffb,
    MINI_MAESTRO_12_PID = 0x008a,
    GET_PARAMETER = 0x81,
    REQUEST_TYPE_VENDOR_DEVICE_IN = 0xc0,
    CHANNEL_MODE_0_3 = 12,
    SERVO0_HOME = 30,
    PARAMS_PER_CHANNEL = 9,
};

static uint16_t get_parameter(libusb_device_handle *device, uint8_t parameter,
                              uint8_t length) {
    unsigned char bytes[2] = {0, 0};
    int transferred = libusb_control_transfer(
        device, REQUEST_TYPE_VENDOR_DEVICE_IN, GET_PARAMETER, 0, parameter,
        bytes, length, 1000);
    if (transferred != length) {
        fprintf(stderr, "GET_PARAMETER %u failed: libusb result %d\n",
                parameter, transferred);
        exit(3);
    }
    return (uint16_t)(bytes[0] | ((uint16_t)bytes[1] << 8));
}

static const char *mode_name(uint8_t mode) {
    static const char *names[] = {"servo", "servo_multiplied", "output", "input"};
    return mode < 4 ? names[mode] : "unknown";
}

static const char *home_mode(uint16_t home) {
    if (home == 0) return "off";
    if (home == 1) return "ignore";
    return "goto";
}

int main(void) {
    libusb_context *context = NULL;
    if (libusb_init(&context) != 0) {
        fputs("Could not initialize libusb.\n", stderr);
        return 1;
    }
    libusb_device_handle *device = libusb_open_device_with_vid_pid(
        context, POLOLU_VID, MINI_MAESTRO_12_PID);
    if (!device) {
        fputs("Mini Maestro 12 (1ffb:008a) not found or not readable.\n", stderr);
        libusb_exit(context);
        return 2;
    }

    uint8_t mode_bytes[3];
    for (uint8_t group = 0; group < 3; group++) {
        mode_bytes[group] = (uint8_t)get_parameter(
            device, (uint8_t)(CHANNEL_MODE_0_3 + group), 1);
    }

    puts("device: 1ffb:008a");
    puts("units: quarter_microseconds");
    puts("channels:");
    for (uint8_t channel = 0; channel < 12; channel++) {
        uint8_t base = (uint8_t)(SERVO0_HOME + PARAMS_PER_CHANNEL * channel);
        uint8_t mode = (uint8_t)((mode_bytes[channel >> 2] >>
                                  ((channel & 3) << 1)) & 3);
        uint16_t home = get_parameter(device, base, 2);
        uint16_t minimum = (uint16_t)(64 * get_parameter(device, base + 2, 1));
        uint16_t maximum = (uint16_t)(64 * get_parameter(device, base + 3, 1));
        uint16_t neutral = get_parameter(device, base + 4, 2);
        uint16_t range = (uint16_t)(127 * get_parameter(device, base + 6, 1));
        uint16_t speed_encoded = get_parameter(device, base + 7, 1);
        uint16_t acceleration = get_parameter(device, base + 8, 1);
        printf("  - channel: %u\n", channel);
        printf("    mode: %s\n", mode_name(mode));
        printf("    home_mode: %s\n", home_mode(home));
        printf("    home: %u\n", home > 1 ? home : 0);
        printf("    minimum: %u\n", minimum);
        printf("    maximum: %u\n", maximum);
        printf("    neutral: %u\n", neutral);
        printf("    range_8bit: %u\n", range);
        printf("    speed_encoded: %u\n", speed_encoded);
        printf("    acceleration: %u\n", acceleration);
    }

    libusb_close(device);
    libusb_exit(context);
    return 0;
}
