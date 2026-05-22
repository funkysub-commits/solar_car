#!/usr/bin/with-contenv bashio

bashio::log.info "Solar Car E-Ink Display starting"

# Talk to Home Assistant through the Supervisor proxy - no long-lived token needed
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

export TITLE="$(bashio::config 'title')"
export SPEED_UNIT="$(bashio::config 'speed_unit')"
export WHEEL_DIAMETER_IN="$(bashio::config 'wheel_diameter_in')"
export GEAR_RATIO="$(bashio::config 'gear_ratio')"
export SPEED_MAX="$(bashio::config 'speed_max')"
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

bashio::log.info "speed=${ENT_SPEED} unit=${SPEED_UNIT} poll=${SPEED_POLL}s"

exec python3 /display.py
