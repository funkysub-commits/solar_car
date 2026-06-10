#!/bin/sh
# Entrypoint for the userspace-gs_usb BESTGO test.
#
# The kernel gs_usb driver claims the SH-C31G (that is what creates can0).
# libusb cannot open the adapter while that driver holds it, so in real mode
# we unbind the kernel driver from the adapter's USB interface first. This
# makes can0 disappear -- intended: this test bypasses the kernel CAN stack
# entirely and talks to the adapter over libusb, the same path the PC tools
# use. (Run with the solar-car-canbus add-on stopped.)
set -e

if [ "$1" != "-dummy" ]; then
    drv=/sys/bus/usb/drivers/gs_usb
    if [ -d "$drv" ]; then
        for link in "$drv"/*:*; do
            [ -e "$link" ] || continue          # no interfaces bound -> skip
            busid=$(basename "$link")
            echo "entrypoint: unbinding kernel gs_usb from ${busid}"
            printf '%s' "$busid" > "$drv/unbind" 2>/dev/null || \
                echo "entrypoint: unbind of ${busid} failed (continuing)" >&2
        done
    else
        echo "entrypoint: kernel gs_usb driver not loaded (nothing to unbind)"
    fi
fi

exec python3 /app/bestgo_gsusb_test.py "$@"
