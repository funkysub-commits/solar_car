"""Small unit/value helpers shared by the rendering and panel code.

The speedometer does NO unit conversion: the value and its unit label come
straight from the configured Home Assistant entity. If mph is wanted from an
rpm source, a template sensor in HA does the conversion (see
display/ha/eink_messages.yaml) and ent_speed points at it.
"""
import config


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def to_display_temp(t_c):
    """Convert a temperature in degrees Celsius to the configured display unit."""
    if t_c is None:
        return None
    return t_c if config.TEMP_UNIT == "C" else (t_c * 9.0 / 5.0 + 32.0)
