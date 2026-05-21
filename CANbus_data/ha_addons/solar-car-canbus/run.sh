#!/usr/bin/with-contenv bashio

bashio::log.info "Solar Car CANbus Reader starting"

CAN_INTERFACE="$(bashio::config 'can_interface')"
CAN_BITRATE="$(bashio::config 'can_bitrate')"
EZKONTROL_DUMMY="$(bashio::config 'ezkontrol_dummy')"
EZKONTROL_PUSH_INTERVAL="$(bashio::config 'ezkontrol_push_interval')"
BESTGO_DUMMY="$(bashio::config 'bestgo_dummy')"
BESTGO_PUSH_INTERVAL="$(bashio::config 'bestgo_push_interval')"

bashio::log.info "Config: iface=${CAN_INTERFACE} bitrate=${CAN_BITRATE}"
bashio::log.info "EZkontrol: dummy=${EZKONTROL_DUMMY} push=${EZKONTROL_PUSH_INTERVAL}s"
bashio::log.info "BESTGO:    dummy=${BESTGO_DUMMY} push=${BESTGO_PUSH_INTERVAL}s"

# The CAN bus is brought up unless BOTH devices are in dummy mode.
if [ "${EZKONTROL_DUMMY}" = "true" ] && [ "${BESTGO_DUMMY}" = "true" ]; then
    bashio::log.info "Both devices in dummy mode -- skipping CAN interface bring-up"
else
    # The SH-C31G (CANable2) USB-CAN adapter sometimes powers up into STM32 DFU
    # mode after a cold boot, so no CAN interface appears. A long USB port
    # power-cycle (long enough to fully drain the board) makes it re-enumerate
    # as the gs_usb CAN adapter. Only do this when the interface is missing, so
    # a normal add-on restart doesn't needlessly drop the bus.
    if [ ! -d "/sys/class/net/${CAN_INTERFACE}" ]; then
        bashio::log.warning "${CAN_INTERFACE} missing -- attempting USB-CAN adapter recovery"

        adapter=""
        for d in /sys/bus/usb/devices/*/; do
            vid="$(cat "${d}idVendor" 2>/dev/null)" || continue
            # 1d50:606f = gs_usb (working), 0483:df11 = STM32 DFU (stuck)
            if [ "${vid}" = "1d50" ] || [ "${vid}" = "0483" ]; then
                adapter="$(basename "${d}")"
                break
            fi
        done

        if [ -z "${adapter}" ]; then
            bashio::log.error "USB-CAN adapter not found on the USB bus"
            exit 1
        fi

        # sysfs name e.g. "1-1.3" -> hub "1-1", port "3"
        hub="${adapter%.*}"
        port="${adapter##*.}"
        bashio::log.info "Power-cycling adapter at hub ${hub} port ${port} (15s off)"
        if ! uhubctl -l "${hub}" -p "${port}" -a cycle -d 15; then
            bashio::log.warning "uhubctl power-cycle reported an error"
        fi

        # Wait for the interface to (re)appear after USB re-enumeration.
        for _ in $(seq 1 20); do
            [ -d "/sys/class/net/${CAN_INTERFACE}" ] && break
            sleep 1
        done
    fi

    if [ ! -d "/sys/class/net/${CAN_INTERFACE}" ]; then
        bashio::log.error "${CAN_INTERFACE} still not present after recovery"
        exit 1
    fi

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

export CAN_INTERFACE CAN_BITRATE
export EZKONTROL_DUMMY EZKONTROL_PUSH_INTERVAL
export BESTGO_DUMMY BESTGO_PUSH_INTERVAL
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

exec python3 /can_reader.py
