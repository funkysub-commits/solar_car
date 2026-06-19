#!/usr/bin/with-contenv bashio

bashio::log.info "Solar Car CANbus Reader starting"

CAN_PORT="$(bashio::config 'can_port')"
CAN_BITRATE="$(bashio::config 'can_bitrate')"
EZKONTROL_DUMMY="$(bashio::config 'ezkontrol_dummy')"
EZKONTROL_PUSH_INTERVAL="$(bashio::config 'ezkontrol_push_interval')"
BESTGO_DUMMY="$(bashio::config 'bestgo_dummy')"
BESTGO_PUSH_INTERVAL="$(bashio::config 'bestgo_push_interval')"

bashio::log.info "Config: port=${CAN_PORT:-auto} bitrate=${CAN_BITRATE}"
bashio::log.info "EZkontrol: dummy=${EZKONTROL_DUMMY} push=${EZKONTROL_PUSH_INTERVAL}s"
bashio::log.info "BESTGO:    dummy=${BESTGO_DUMMY} push=${BESTGO_PUSH_INTERVAL}s"

# The SH-C31G runs slcan firmware: it enumerates as a CDC-serial port, NOT a
# SocketCAN interface, so there's no `ip link` bring-up. can_reader.py opens
# the port with python-can's slcan backend and auto-detects it if CAN_PORT is
# blank. We only log what we find here; can_reader re-detects on each retry, so
# a late/replugged adapter is still picked up without restarting the add-on.
if [ "${EZKONTROL_DUMMY}" = "true" ] && [ "${BESTGO_DUMMY}" = "true" ]; then
    bashio::log.info "Both devices in dummy mode -- no adapter needed"
elif [ -n "${CAN_PORT}" ]; then
    bashio::log.info "Using configured serial port ${CAN_PORT}"
else
    found=""
    for p in /dev/serial/by-id/* /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0; do
        if [ -e "$p" ]; then found="$p"; break; fi
    done
    if [ -n "${found}" ]; then
        bashio::log.info "Detected slcan adapter at ${found}"
    else
        bashio::log.warning "No serial CAN adapter found (/dev/ttyACM*). Starting anyway -- canadapter_status will be 0 and can_reader will keep retrying."
    fi
fi

export CAN_PORT CAN_BITRATE
export EZKONTROL_DUMMY EZKONTROL_PUSH_INTERVAL
export BESTGO_DUMMY BESTGO_PUSH_INTERVAL
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

exec python3 /can_reader.py
