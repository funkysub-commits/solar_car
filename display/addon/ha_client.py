"""Home Assistant REST access: raw entity I/O and the typed readers the main
loop uses. All network failures degrade to None/empty - the display keeps
showing the last known values and the staleness layer reports the gap.

Connection health is tracked across every request so the add-on can tell
"Home Assistant itself is unreachable" apart from "the CAN sensors stopped
updating" - two very different failures that would otherwise look identical
on the panel and in the logs."""
import logging
import time
from datetime import datetime, timezone

import requests

import config

# Consecutive-failure tracking for ha_unreachable().
_UNREACHABLE_AFTER_FAILS = 3      # this many consecutive request failures...
_UNREACHABLE_AFTER_SECS = 15      # ...spanning at least this long
_WARN_EVERY = 60                  # rate-limit for the unreachable log line
_fail_count = 0
_first_fail = None
_last_warn = 0.0


def _record(ok):
    """Track request outcomes; log unreachability once a minute, not per-poll."""
    global _fail_count, _first_fail, _last_warn
    now = time.time()
    if ok:
        if ha_unreachable():
            logging.info("Home Assistant reachable again")
        _fail_count = 0
        _first_fail = None
        return
    _fail_count += 1
    if _first_fail is None:
        _first_fail = now
    if ha_unreachable() and now - _last_warn >= _WARN_EVERY:
        _last_warn = now
        logging.warning(f"Home Assistant unreachable - {_fail_count} consecutive "
                        f"request failures over {now - _first_fail:.0f}s")


def ha_unreachable():
    """True once requests have failed consecutively for a while. Used to show
    'Home Assistant unreachable' instead of the misleading 'CAN bus not
    connected' that pure staleness inference would produce during an HA outage."""
    return (_fail_count >= _UNREACHABLE_AFTER_FAILS
            and _first_fail is not None
            and time.time() - _first_fail >= _UNREACHABLE_AFTER_SECS)


def ha_get(entity):
    """Return (state, attributes, last_reported_iso) for an entity. last_reported
    is preferred over last_updated because it advances on every push, even when
    the state value hasn't changed - which is what we want for staleness."""
    try:
        r = requests.get(f"{config.HA_URL}/api/states/{entity}",
                         headers=config.HEADERS, timeout=5)
        if r.status_code == 404:        # entity simply doesn't exist - the
            _record(True)               # connection itself is healthy
            return None, {}, None
        r.raise_for_status()
        j = r.json()
        _record(True)
        return (j.get("state"), j.get("attributes", {}),
                j.get("last_reported") or j.get("last_updated"))
    except Exception as e:
        logging.debug(f"fetch {entity} failed: {e}")
        _record(False)
        return None, {}, None


def ha_post_state(entity, state, attributes):
    """Create/update an HA entity's state via the REST API. Used to publish the
    live warning list to sensor.eink_warnings (states POSTed this way are
    transient - they vanish on HA restart and are simply re-published)."""
    try:
        requests.post(f"{config.HA_URL}/api/states/{entity}",
                      headers={**config.HEADERS, "Content-Type": "application/json"},
                      json={"state": str(state), "attributes": attributes}, timeout=5)
        _record(True)
    except Exception as e:
        logging.debug(f"publish {entity} failed: {e}")
        _record(False)


def ha_call_service(domain, service, data):
    """Call an HA service via the REST API (e.g. input_text.set_value)."""
    try:
        requests.post(f"{config.HA_URL}/api/services/{domain}/{service}",
                      headers={**config.HEADERS, "Content-Type": "application/json"},
                      json=data, timeout=5)
        _record(True)
    except Exception as e:
        logging.debug(f"service {domain}.{service} failed: {e}")
        _record(False)


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
    """Read the free-text message entity (input_text). Returns the text, ''
    if the helper is unset/cleared, or None when the request itself failed -
    so a transient HA hiccup doesn't clobber (and then flicker) the message."""
    state, attrs, lu = ha_get(entity)
    if state is None and not attrs and lu is None:
        return None                      # fetch failed / entity missing
    if state in ("", "unknown", "unavailable"):
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
    """Write the hidden-key set back to input_text.eink_hidden. Callers fit
    the set to the helper's 255-char cap first (alerts.fit_hidden); if an
    oversized set still reaches here, cut at a key boundary - never mid-key,
    which would corrupt the whole list."""
    value = ",".join(sorted(keys))
    if len(value) > 255:
        value = value[:256].rsplit(",", 1)[0]
        logging.warning(f"hidden-key list over 255 chars - truncated to: {value}")
    ha_call_service("input_text", "set_value",
                    {"entity_id": config.ENT_HIDDEN, "value": value})
