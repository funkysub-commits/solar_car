#!/usr/bin/with-contenv bashio

bashio::log.info "Solar Car CANbus Reader starting"

CAN_BITRATE="$(bashio::config 'can_bitrate')"
EZKONTROL_DUMMY="$(bashio::config 'ezkontrol_dummy')"
EZKONTROL_PUSH_INTERVAL="$(bashio::config 'ezkontrol_push_interval')"
BESTGO_DUMMY="$(bashio::config 'bestgo_dummy')"
BESTGO_PUSH_INTERVAL="$(bashio::config 'bestgo_push_interval')"

bashio::log.info "Config: bitrate=${CAN_BITRATE}"
bashio::log.info "EZkontrol: dummy=${EZKONTROL_DUMMY} push=${EZKONTROL_PUSH_INTERVAL}s"
bashio::log.info "BESTGO:    dummy=${BESTGO_DUMMY} push=${BESTGO_PUSH_INTERVAL}s"

# The SH-C31G runs candlelight/gs_usb firmware: the kernel gs_usb driver exposes
# it as SocketCAN can0. Bring the interface up here (needs NET_ADMIN + host
# network), then can_reader.py opens it with python-can's socketcan backend and
# keeps retrying if it isn't ready yet (late/replugged adapter).
if [ "${EZKONTROL_DUMMY}" = "true" ] && [ "${BESTGO_DUMMY}" = "true" ]; then
    bashio::log.info "Both devices in dummy mode -- no adapter needed"
elif [ -d /sys/class/net/can0 ]; then
    ip link set can0 down 2>/dev/null
    if ip link set can0 type can bitrate "${CAN_BITRATE}" && ip link set can0 up; then
        bashio::log.info "can0 up @ ${CAN_BITRATE} bps (SocketCAN)"
    else
        bashio::log.warning "can0 bring-up FAILED; can_reader will retry (canadapter_status=0)"
    fi
else
    bashio::log.warning "can0 not present (adapter unplugged / in DFU / gs_usb not bound). can_reader will retry."
fi

export CAN_BITRATE
export EZKONTROL_DUMMY EZKONTROL_PUSH_INTERVAL
export BESTGO_DUMMY BESTGO_PUSH_INTERVAL
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

exec python3 /can_reader.py
