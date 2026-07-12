"""Home Assistant REST access: raw entity I/O and the typed readers the main
loop uses. All network failures degrade to None/empty - the display keeps
showing the last known values and the staleness layer reports the gap.

Connection health is tracked across every request so the add-on can tell
"Home Assistant itself is unreachable" apart from "the CAN sensors stopped
updating" - two very different failures that would otherwise look identical
on the panel and in the logs."""
import base64
import io
import logging
import threading
import time
from datetime import datetime, timezone

import requests

import config

# One shared session for all HA/Supervisor requests: connection reuse cuts
# per-request latency dramatically (the loop makes 3-15 requests per pass),
# which also bounds how long a shutdown can lag behind SIGTERM.
_session = requests.Session()

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


# --- Header connection block ------------------------------------------------
# Two networks the crew might join to reach the dashboard during the race:
#   * Router  - an on-car Ethernet router plugged into the Pi (no internet):
#               a stable LAN address that supports several people right by the
#               car, used as the backup when out of cell range. The Pi's wired
#               (eth) IPv4.
#   * Hotspot - a phone's hotspot the Pi joins for cell coverage: its LAN
#               address, for people who connect to that hotspot and so also get
#               an internet connection. The Pi's wireless (wlan) IPv4.
# Both are local addresses on their respective networks; either link may be
# missing, so there is always at least one row to show (or "Pi Offline").
#
# The addresses are cached and refreshed off the hot loop (refresh_network()
# spawns a short-lived thread), so the per-loop draw never blocks; the last
# good value is kept while a refresh is failing.
_router_ip = None         # on-car Ethernet router LAN IPv4 (no internet)
_wifi_ip = None           # phone-hotspot LAN IPv4 (Wi-Fi, has internet)
_net_at = 0.0
_net_lock = threading.Lock()
_net_refreshing = False
_NET_TTL = 240.0          # refresh the addresses at most this often ("once in a while")
_NET_RETRY = 30.0         # ...but retry this soon after a FAILED refresh

# Supervisor's own docker network - never a real connect address.
_INTERNAL_PREFIXES = ("172.30.", "172.17.", "127.")


def _query_interfaces():
    """(ethernet_ip, wireless_ip) from Supervisor /network/info, CIDR suffix
    stripped; each is None when that kind of link currently has no IPv4.
    Returns None (not a tuple) when the request itself fails, so the caller can
    tell 'link is down' apart from 'couldn't ask' and keep the last good value."""
    try:
        r = _session.get(f"{config.SUPERVISOR_URL}/network/info",
                         headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"},
                         timeout=5)
        r.raise_for_status()
        ifaces = (r.json().get("data") or {}).get("interfaces") or []
    except Exception as e:
        logging.debug(f"network/info failed: {e}")
        return None
    # primary/connected first so we pick the live link when several exist
    ifaces.sort(key=lambda i: (not i.get("primary"), not i.get("connected")))
    eth = wifi = None
    for i in ifaces:
        name = (i.get("interface") or "")
        typ = (i.get("type") or "").lower()
        addrs = ((i.get("ipv4") or {}).get("address")) or []
        ip = next((a.split("/")[0] for a in addrs
                   if not a.startswith(_INTERNAL_PREFIXES)), None)
        if not ip:
            continue
        if (typ == "wireless" or name.startswith(("wlan", "wl"))):
            if wifi is None:
                wifi = ip
        elif eth is None:                       # ethernet, or any other wired link
            eth = ip
    return eth, wifi


def _do_refresh():
    global _router_ip, _wifi_ip, _net_refreshing, _net_at
    ok = False
    try:
        res = _query_interfaces()
        if res is not None:                     # reached Supervisor - trust it,
            _router_ip, _wifi_ip = res          # even to clear an unplugged link
            ok = True
    finally:
        with _net_lock:
            _net_refreshing = False
            if not ok:
                # a failed refresh must not burn the whole TTL (the header
                # would show "Pi Offline"/stale IPs for minutes after a boot
                # race) - allow the next attempt after a short retry delay
                _net_at = time.time() - _NET_TTL + _NET_RETRY


def refresh_network(force=False):
    """Refresh the cached Router/Hotspot LAN addresses, at most once per
    _NET_TTL. Runs in a background thread so the draw loop never blocks on the
    Supervisor lookup; pass force=True at startup to do one inline fetch so the
    very first frame already has the addresses."""
    global _net_at, _net_refreshing
    now = time.time()
    with _net_lock:
        if not force and (now - _net_at) < _NET_TTL:
            return
        if _net_refreshing:
            return
        _net_refreshing = True
        _net_at = now
    if force:
        _do_refresh()
    else:
        threading.Thread(target=_do_refresh, name="net-refresh", daemon=True).start()


def connection_lines():
    """The header connection rows as a list of (label, value) tuples from the
    newest cached addresses - up to two: 'Router' (the on-car Ethernet router
    LAN IP, for crew by the car, no internet) and 'Hotspot' (the Pi's LAN IP on
    the phone hotspot, for people who join that hotspot and so also get an
    internet connection). Either may be absent; falls back to a single
    ('', 'Pi Offline') row when Home Assistant is unreachable or no address is
    known at all."""
    if ha_unreachable():
        return [("", "Pi Offline")]
    port = config.HA_PORT
    rows = []
    if _router_ip:
        rows.append(("Router", f"{_router_ip}:{port}"))
    if _wifi_ip:
        rows.append(("Hotspot", f"{_wifi_ip}:{port}"))
    return rows or [("", "Pi Offline")]


def _qr_data_uri(text):
    """A scannable QR of `text` as a PNG data: URI (renders inline in a Home
    Assistant markdown card, offline - no external QR service, which matters on
    the car's on-car router LAN that has no internet). Returns None if the
    qrcode library is unavailable, so publishing degrades to just the URL text
    rather than failing. Imported lazily so the module still loads on a PC
    without qrcode installed (the tests import this module)."""
    try:
        import qrcode
    except Exception as e:
        logging.debug(f"qrcode unavailable, skipping QR: {e}")
        return None
    try:
        qr = qrcode.QRCode(border=2, box_size=6,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logging.debug(f"QR render failed: {e}")
        return None


# Last-published (state, qr) per IP entity, so an unchanged link doesn't re-POST
# every heartbeat and we only re-render a QR when the URL actually changes.
_ip_pub = {}
_ip_pub_time = 0.0


def publish_ip_sensors():
    """Publish sensor.pi_router_ip / sensor.pi_hotspot_ip from the cached header
    addresses so a dashboard can show a "scan to open Home Assistant" QR per
    link. State is the LAN IP when the link is up (attributes: url, connected,
    qr data-URI) and "unavailable" when it is down, so a conditional card can
    show each QR only while connected. Republished on change and on a heartbeat
    (PUBLISH_EVERY) so the REST-published states self-heal after an HA restart."""
    global _ip_pub_time
    now = time.time()
    heartbeat = (now - _ip_pub_time) >= config.PUBLISH_EVERY
    targets = [(config.ENT_PI_ROUTER_IP, "Pi Router IP", "mdi:router-network", _router_ip),
               (config.ENT_PI_HOTSPOT_IP, "Pi Hotspot IP", "mdi:wifi", _wifi_ip)]
    posted = False
    for entity, name, icon, ip in targets:
        if ip:
            url = f"http://{ip}:{config.HA_PORT}"
            prev_url, qr = _ip_pub.get(entity, (None, None))
            if prev_url != url:                       # url changed -> new QR
                qr = _qr_data_uri(url)
            elif not heartbeat:
                continue                              # unchanged, not due -> skip
            attrs = {"friendly_name": name, "icon": icon,
                     "url": url, "connected": True}
            if qr:
                attrs["qr"] = qr
            ha_post_state(entity, ip, attrs)
            _ip_pub[entity] = (url, qr)
            posted = True
        else:
            if _ip_pub.get(entity, (None, None))[0] == "unavailable" and not heartbeat:
                continue
            ha_post_state(entity, "unavailable",
                          {"friendly_name": name, "icon": icon, "connected": False})
            _ip_pub[entity] = ("unavailable", None)
            posted = True
    if posted or heartbeat:
        _ip_pub_time = now


def ha_get_ex(entity):
    """ha_get plus an `ok` flag: (state, attributes, last_reported_iso, ok).
    ok is True whenever the request itself succeeded - a 404 (entity simply
    missing) still counts as ok. ok=False means HA couldn't be asked at all,
    so callers that must tell "explicitly off/absent" apart from "couldn't
    read" (the power toggle, the hidden-warnings list) can keep their last
    known value instead of misreading the failure."""
    try:
        r = _session.get(f"{config.HA_URL}/api/states/{entity}",
                         headers=config.HEADERS, timeout=5)
        if r.status_code == 404:        # entity simply doesn't exist - the
            _record(True)               # connection itself is healthy
            return None, {}, None, True
        r.raise_for_status()
        j = r.json()
        _record(True)
        return (j.get("state"), j.get("attributes", {}),
                j.get("last_reported") or j.get("last_updated"), True)
    except Exception as e:
        logging.debug(f"fetch {entity} failed: {e}")
        _record(False)
        return None, {}, None, False


def ha_get(entity):
    """Return (state, attributes, last_reported_iso) for an entity. last_reported
    is preferred over last_updated because it advances on every push, even when
    the state value hasn't changed - which is what we want for staleness."""
    return ha_get_ex(entity)[:3]


def ha_post_state(entity, state, attributes):
    """Create/update an HA entity's state via the REST API. Used to publish the
    live warning list to sensor.eink_warnings (states POSTed this way are
    transient - they vanish on HA restart and are simply re-published)."""
    try:
        _session.post(f"{config.HA_URL}/api/states/{entity}",
                      headers={**config.HEADERS, "Content-Type": "application/json"},
                      json={"state": str(state), "attributes": attributes}, timeout=5)
        _record(True)
    except Exception as e:
        logging.debug(f"publish {entity} failed: {e}")
        _record(False)


def ha_call_service(domain, service, data):
    """Call an HA service via the REST API (e.g. input_text.set_value)."""
    try:
        _session.post(f"{config.HA_URL}/api/services/{domain}/{service}",
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
    False = down, None = unknown. None tells the caller to fall back to
    inferring the same fact from sensor staleness, and covers only a genuinely
    unknowable state: the entity missing/unavailable, or a state we can't parse.

    An explicit reading is TRUSTED regardless of the entity's timestamp. The
    CANbus app re-pushes these sensors on a heartbeat with an unchanged state
    and unchanged attributes, and Home Assistant does not advance last_updated
    in that case - so an age check here read a healthy, steadily-republished
    '1' as "stale" and inferred a CAN adapter outage that wasn't happening."""
    state, _, _ = ha_get(entity)
    if state in (None, "", "unknown", "unavailable"):
        return None
    s = str(state).strip().lower()
    if s in _HEALTH_TRUE:
        return True
    if s in _HEALTH_FALSE:
        return False
    return None


_CHARGING_TRUE = {"on", "true", "charging", "charge", "yes", "1", "up"}


def read_charging(entity):
    """Whether the pack is charging - drives the lightning bolt on the battery
    icon. True only for a clearly-charging state; anything unknown/unavailable/
    missing or a fetch failure reads as False (no bolt), so a hiccup never
    flashes a spurious charging mark."""
    state, _, _ = ha_get(entity)
    if state in (None, "", "unknown", "unavailable"):
        return False
    return str(state).strip().lower() in _CHARGING_TRUE


def set_message(entity, text):
    """Write the free-text message helper (input_text.set_value). Called once at
    start-up to reset the MESSAGE box to the configured startup_message, so a
    note left over from the previous run doesn't linger on the panel. input_text
    caps its value at 255 chars, so a longer message is truncated rather than
    rejected outright."""
    value = str(text)[:255]
    ha_call_service(entity.split(".", 1)[0], "set_value",
                    {"entity_id": entity, "value": value})


def read_hidden():
    """Return the set of warning keys the user has chosen to hide (read from the
    comma-separated input_text.eink_hidden helper), or None when the request
    itself failed - a failed read must not look like "nothing hidden", or one
    timed-out poll would transiently un-hide every hidden warning chip."""
    state, _, _, ok = ha_get_ex(config.ENT_HIDDEN)
    if not ok:
        return None
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
    
def play_sound(file, entity_id=config.AUX_ALARM_PLAYER):
    """Play a sound file through a media_player (VLC over telnet by default).
    `file` is a path under Home Assistant's local "media" folder - e.g.
    "aux_low.mp3" plays media/aux_low.mp3. Best-effort: any failure degrades to
    a logged miss (inside ha_call_service) rather than disturbing the loop."""
    data = {
        "entity_id": entity_id,
        "media_content_id": f"media_local/{file}",
        "media_content_type": "music",
    }
    ha_call_service("media_player", "play_media", data)
