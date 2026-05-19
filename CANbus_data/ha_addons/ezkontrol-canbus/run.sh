#!/usr/bin/with-contenv bashio

bashio::log.info "EZkontrol CANbus Reader starting"

CAN_INTERFACE="$(bashio::config 'can_interface')"
CAN_BITRATE="$(bashio::config 'can_bitrate')"
PUSH_INTERVAL="$(bashio::config 'push_interval')"
DUMMY_MODE="$(bashio::config 'dummy_mode')"

bashio::log.info "Config: iface=${CAN_INTERFACE} bitrate=${CAN_BITRATE} push=${PUSH_INTERVAL}s dummy=${DUMMY_MODE}"

if [ "${DUMMY_MODE}" != "true" ]; then
    bashio::log.info "Bringing up ${CAN_INTERFACE} at ${CAN_BITRATE} bps"
    ip link set "${CAN_INTERFACE}" down 2>/dev/null || true
    if ! ip link set "${CAN_INTERFACE}" type can bitrate "${CAN_BITRATE}"; then
        bashio::log.error "Failed to configure ${CAN_INTERFACE}"
        exit 1
    fi
    if ! ip link set "${CAN_INTERFACE}" up; then
        bashio::log.error "Failed to bring up ${CAN_INTERFACE}"
        exit 1
    fi
    bashio::log.info "${CAN_INTERFACE} is up"
fi

export CAN_INTERFACE CAN_BITRATE PUSH_INTERVAL DUMMY_MODE
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

exec python3 /can_reader.py
