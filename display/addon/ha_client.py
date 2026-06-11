"""Home Assistant REST access: raw entity I/O and the typed readers the main
loop uses. All network failures degrade to None/empty - the display keeps
showing the last known values and the staleness layer reports the gap."""
import logging
from datetime import datetime, timezone

import requests

import config


def ha_get(entity):
    """Return (state, attributes, last_reported_iso) for an entity. last_reported
    is preferred over last_updated because it advances on every push, even when
    the state value hasn't changed - which is what we want for staleness."""
    try:
        r = requests.get(f"{config.HA_URL}/api/states/{entity}",
                         headers=config.HEADERS, timeout=5)
        r.raise_for_status()
        j = r.json()
        return (j.get("state"), j.get("attributes", {}),
                j.get("last_reported") or j.get("last_updated"))
    except Exception as e:
        logging.debug(f"fetch {entity} failed: {e}")
        return None, {}, None


def ha_post_state(entity, state, attributes):
    """Create/update an HA entity's state via the REST API. Used to publish the
    live warning list to sensor.eink_warnings (states POSTed this way are
    transient - they vanish on HA restart and are simply re-published)."""
    try:
        requests.post(f"{config.HA_URL}/api/states/{entity}",
                      headers={**config.HEADERS, "Content-Type": "application/json"},
                      json={"state": str(state), "attributes": attributes}, timeout=5)
    except Exception as e:
        logging.debug(f"publish {entity} failed: {e}")


def ha_call_service(domain, service, data):
    """Call an HA service via the REST API (e.g. input_text.set_value)."""
    try:
        requests.post(f"{config.HA_URL}/api/services/{domain}/{service}",
                      headers={**config.HEADERS, "Content-Type": "application/json"},
                      json=data, timeout=5)
    except Exception as e:
        logging.debug(f"service {domain}.{service} failed: {e}")


def entity_age_seconds(last_iso):
    """How long since this HA timestamp, in seconds. inf if missing/bad."""
    if not last_iso:
        return float("inf")
    try:
        ts = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return float("inf")


def read_number(entity):
    """Return (float value, unit, last_iso) for a numeric entity."""
    state, attrs, lu = ha_get(entity)
    unit = attrs.get("unit_of_measurement", "")
    if state in (None, "", "unknown", "unavailable"):
        return None, unit, lu
    try:
        return float(state), unit, lu
    except (TypeError, ValueError):
        return None, unit, lu


def read_temp_c(entity):
    """Read a temperature entity and normalise to degrees Celsius.
    Returns (value_c or None, last_iso)."""
    val, unit, lu = read_number(entity)
    if val is None:
        return None, lu
    if unit and "F" in unit.upper():       # Pi sensor reports Fahrenheit
        val = (val - 32.0) * 5.0 / 9.0
    return val, lu


def read_message(entity):
    """Read the free-text message entity (input_text), or '' if unset."""
    state, _, _ = ha_get(entity)
    if state in (None, "", "unknown", "unavailable"):
        return ""
    return str(state).strip()


_HEALTH_TRUE = {"on", "true", "connected", "ok", "online", "yes", "up", "healthy", "1"}
_HEALTH_FALSE = {"off", "false", "disconnected", "not connected", "error",
                 "offline", "no", "down", "unhealthy", "0"}


def read_health(entity):
    """Tri-state read of a connectivity/health entity: True = healthy,
    False = down, None = unknown (entity missing, unavailable, or an
    unrecognised state). None tells the caller to fall back to inferring the
    same fact from sensor staleness - so the display keeps working before the
    CANbus app publishes these sensors."""
    state, _, _ = ha_get(entity)
    if state in (None, "", "unknown", "unavailable"):
        return None
    s = str(state).strip().lower()
    if s in _HEALTH_TRUE:
        return True
    if s in _HEALTH_FALSE:
        return False
    return None


def read_hidden():
    """Return the set of warning keys the user has chosen to hide (read from the
    comma-separated input_text.eink_hidden helper)."""
    state, _, _ = ha_get(config.ENT_HIDDEN)
    if not state or state in ("unknown", "unavailable"):
        return set()
    return {p.strip() for p in str(state).split(",") if p.strip()}


def set_hidden(keys):
    """Write the hidden-key set back to input_text.eink_hidden."""
    ha_call_service("input_text", "set_value",
                    {"entity_id": config.ENT_HIDDEN,
                     "value": ",".join(sorted(keys))[:255]})
