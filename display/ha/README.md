# E-ink warnings / message system — Home Assistant setup

The add-on ([../addon/display.py](../addon/display.py)) keeps **plain user messages**
and **warnings** separate:

* the user's free-text message (`input_text.eink_message`) shows in the **MESSAGE
  box** (bottom-left of the panel);
* **warnings** fill the **bottom warning bar** left→right, highest priority leftmost,
  with a `+N` pill at the right when more are active than fit.

It also publishes the live warning list back to Home Assistant so you can see every
warning and hide individual ones from the screen.

## What the add-on does

* Fills the bottom bar with active warning chips (highest priority leftmost); a `+N`
  pill shows how many didn't fit.
* Warning priority (highest first): **Home Assistant unreachable** → **CAN adapter
  disconnected** → **BESTGO disconnected** → **EZkontrol disconnected** → **high temp
  (hotter = higher, capped below the device warnings)** → **a sensor that stopped
  updating**.
* The three CAN devices are tracked independently from the canbus app's health
  sensors (`sensor.canadapter_status` / `bestgo_status` / `ezkontrol_status`, 1/0),
  falling back to staleness inference when a sensor is unknown or itself stale.
* Any value whose source has stopped updating gets a small `!` mark, scoped to the
  device that feeds it: a BESTGO dropout marks only SoC/voltage/BATT temp, an
  EZkontrol dropout only speed/motor/EZK temp, the adapter marks every CAN value.
  A steady-but-still-reported value is **not** marked.
* Publishes `sensor.eink_warnings` (state = active warning count) with attributes
  `warnings`, `lines`, `keys_visible`, `keys_hidden`.
* Reads `input_text.eink_hidden` (comma-separated warning keys) and removes those
  from the e-paper. Keys whose warning is no longer active are pruned automatically,
  so a warning that clears and later returns shows again.

## Apply (two pieces)

### 1. Helper + hide scripts + mph template — `eink_messages.yaml`

Defines `input_text.eink_hidden`, `script.eink_hide` / `eink_unhide` /
`eink_unhide_all`, and `sensor.solar_car_speed` (the rpm→mph template sensor).

Easiest is the HA **packages** mechanism:

1. In `configuration.yaml`, add once (if you don't already have it):
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Copy [eink_messages.yaml](eink_messages.yaml) to `/config/packages/eink_messages.yaml`.
3. **Developer tools → YAML → Check configuration**, then **Restart** Home Assistant.

(Alternatively: create the `input_text.eink_hidden` text helper in the UI — max length
255 — and paste the three `script:` blocks into your existing `scripts.yaml`.)

> **User message helper:** the MESSAGE box shows `input_text.eink_message`. If you
> don't already have that helper, create it (UI text helper, max 255) or uncomment the
> `eink_message:` block in `eink_messages.yaml`. Without it the box just shows
> "- no message -".

### 2. Dashboard section — `dashboard_warnings_section.yaml`

Adds an **E-Paper Warnings** section that lists every active warning (hidden ones
marked), a **Hide** button for each warning currently shown, a **Show** (unhide)
button for each one currently hidden, an **Unhide all** button, and a field to type
the e-paper message. Add it to both the **Solar Car** and **All Data** dashboards.

1. Open the dashboard → 3-dot menu → **Edit dashboard** → 3-dot menu →
   **Raw configuration editor**.
2. Paste the `type: grid` block from
   [dashboard_warnings_section.yaml](dashboard_warnings_section.yaml) as a new entry in
   the view's `sections:` list.
3. **Save**.

Each control only appears for a warning that is actually active right now, and each
warning shows exactly one control at a time — **Hide** while it's on the e-paper,
**Show** once you've hidden it — so you can hide and un-hide warnings individually
(not just "unhide all"). Button visibility keys off `input_text.eink_hidden`
directly, so a tap flips the control instantly rather than waiting for the add-on to
re-publish `sensor.eink_warnings`.

### 3. "Connect to Pi" QR section — `dashboard_qr_section.yaml`

Adds a **Connect to Pi** section with a QR code for each LAN link the Pi has —
**Router** (on-car Ethernet, no internet) and **Hotspot** (phone Wi-Fi) — each shown
only while that link is connected. Scanning opens Home Assistant at that address. The
QR images are generated **offline by the add-on** (no external QR service, so they
work on the car's no-internet router LAN) and published as the `qr` attribute of
`sensor.pi_router_ip` / `sensor.pi_hotspot_ip`. Paste the block from
[dashboard_qr_section.yaml](dashboard_qr_section.yaml) into the view's `sections:`
list the same way.

> These two sensors carry a small base64 PNG in their `qr` attribute. If you want to
> keep it out of the recorder database, exclude `sensor.pi_router_ip` and
> `sensor.pi_hotspot_ip` in your `recorder:` config.

## Warning keys (for reference)

| key | shown when |
|-----|------------|
| `ha` | the add-on can't reach Home Assistant at all → "Home Assistant unreachable" (replaces the CAN/staleness deductions, unknowable during an HA outage) |
| `can_adapter` | the USB-CAN adapter/bus is down — `sensor.canadapter_status` 0, else inferred from every CAN value being stale; marks `!` on all CAN values |
| `can_bestgo` | the battery isn't on CAN — `sensor.bestgo_status` 0, else inference; marks `!` on SoC, voltage, BATT temp |
| `can_ezk` | the EZkontrol isn't on CAN — `sensor.ezkontrol_status` 0, else inference; marks `!` on speed, motor temp, EZK temp |
| `aux_batt` | the auxiliary (12V) battery's status sensor reads disconnected (only while `aux_enabled`) → "AUX battery disconnected" |
| `aux_low` | the aux battery SoC has dropped to/below the highest configured `aux_low_levels` percentage → "AUX battery low nn%" (also sounds `aux_low_sound` as it crosses each level) |
| `temp_t_motor` / `temp_t_ezk` / `temp_t_batt` / `temp_t_pi` | that temperature ≥ `temp_warn` (live reading only) |

The plain user message is **not** a warning - it lives in the MESSAGE box, set via
`input_text.eink_message`.

To hide a warning manually you can also type its key into `input_text.eink_hidden`
(comma-separated); to unhide, remove it or clear the field.
