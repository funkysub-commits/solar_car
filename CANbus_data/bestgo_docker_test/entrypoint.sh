#!/bin/sh
# Container entrypoint: bring up the CAN interface, then run the decode test.
#
# In real mode it configures the host's can0 (the container runs with
# --network host --cap-add NET_ADMIN). In -dummy mode it skips the
# interface entirely and runs on synthetic frames.
set -e

CAN_CHANNEL="${CAN_CHANNEL:-can0}"
CAN_BITRATE="${CAN_BITRATE:-500000}"

if [ "$1" != "-dummy" ]; then
    echo "entrypoint: bringing up ${CAN_CHANNEL} at ${CAN_BITRATE} bps"
    ip link set "${CAN_CHANNEL}" down 2>/dev/null || true
    if ! ip link set "${CAN_CHANNEL}" type can bitrate "${CAN_BITRATE}"; then
        echo "entrypoint: failed to configure ${CAN_CHANNEL} -- is the" \
             "SH-C31G plugged in and out of DFU mode?" >&2
        exit 1
    fi
    ip link set "${CAN_CHANNEL}" up
    echo "entrypoint: ${CAN_CHANNEL} is up"
fi

exec python3 /app/bestgo_logtest.py "$@"
