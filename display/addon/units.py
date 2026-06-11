"""Small unit/value helpers shared by the rendering and panel code."""
import math

import config


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rpm_to_speed(rpm):
    """Convert raw motor rpm into the configured speedometer unit."""
    if rpm is None:
        return None
    if config.SPEED_UNIT == "rpm":
        return rpm
    wheel_rpm = rpm / config.GEAR_RATIO if config.GEAR_RATIO else rpm
    inches_per_min = wheel_rpm * math.pi * config.WHEEL_DIAMETER_IN
    if config.SPEED_UNIT in ("kmh", "km/h"):
        return inches_per_min * 60.0 * 0.0254 / 1000.0      # km/h
    return inches_per_min * 60.0 / 63360.0                  # mph


def to_display_temp(t_c):
    """Convert a temperature in degrees Celsius to the configured display unit."""
    if t_c is None:
        return None
    return t_c if config.TEMP_UNIT == "C" else (t_c * 9.0 / 5.0 + 32.0)
