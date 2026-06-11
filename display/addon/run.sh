#!/usr/bin/with-contenv bashio

bashio::log.info "Solar Car E-Ink Display starting"

# Talk to Home Assistant through the Supervisor proxy - no long-lived token needed
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

export TITLE="$(bashio::config 'title')"
export SPEED_MAX="$(bashio::config 'speed_max')"
export TEMP_UNIT="$(bashio::config 'temp_unit')"
export TEMP_MAX="$(bashio::config 'temp_max')"
export TEMP_WARN="$(bashio::config 'temp_warn')"
export SPEED_POLL="$(bashio::config 'speed_poll')"
export SLOW_POLL="$(bashio::config 'slow_poll')"
export FULL_REFRESH_EVERY="$(bashio::config 'full_refresh_every')"
export IDLE_SLEEP="$(bashio::config 'idle_sleep')"
export ENT_SPEED="$(bashio::config 'ent_speed')"
export ENT_T_MOTOR="$(bashio::config 'ent_t_motor')"
export ENT_T_EZK="$(bashio::config 'ent_t_ezk')"
export ENT_T_BATT="$(bashio::config 'ent_t_batt')"
export ENT_T_PI="$(bashio::config 'ent_t_pi')"
export ENT_SOC="$(bashio::config 'ent_soc')"
export ENT_VOLTAGE="$(bashio::config 'ent_voltage')"
export ENT_MESSAGE="$(bashio::config 'ent_message')"
export ENT_POWER="$(bashio::config 'ent_power')"
export ENT_CAN_BUS="$(bashio::config 'ent_can_bus')"
export ENT_CAN_BATT="$(bashio::config 'ent_can_battery')"
export ENT_CAN_EZK="$(bashio::config 'ent_can_ezkontrol')"

bashio::log.info "speed=${ENT_SPEED} (value+unit from HA) poll=${SPEED_POLL}s"

exec python3 /display.py
