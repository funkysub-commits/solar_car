# Home Assistant config for the CAN reader

Home Assistant YAML that complements the `solar-car-canbus` add-on. (The add-on
itself lives in `../ha_addons/solar-car-canbus/`; the e-ink display's HA YAML is
in `../../display/ha/`.)

## What's here

| Path | Role |
| --- | --- |
| `python_scripts/bootstrap_canbus_entities.py` | Creates every `sensor.ezkontrol_*` / `sensor.bestgo_*` as `unavailable` if it doesn't already exist. **Auto-generated** from `EZ_SENSORS` / `BG_SENSORS` — do not hand-edit. |
| `packages/canbus_bootstrap.yaml` | Enables `python_script` and adds the automation that runs the script on the `homeassistant.start` event. |
| `packages/canbus_controls.yaml` | Defines `input_number.canbus_update_interval` — the **update interval** (seconds) the add-on (0.11.0+) polls; it overrides both devices' push intervals and the CSV log tick live (0.1–60 s). Only needs an *input_number* reload, not a restart (`deploy_addon.py --with-packages` does this). |
| `dashboard_telemetry_section.yaml` | Dashboard card: the interval box with 0.2 / 0.5 / 2 / 10 s preset buttons and download links to the add-on's export page (`http://<pi>:8099/`, also the "Telemetry Export" sidebar panel), which builds a CSV from HA's recorded history on demand. |

## Why

The add-on publishes sensors by POSTing to HA's REST `/api/states`, and (since
the fresh-frame change) only once a CAN frame has populated each field. Those
REST-created states also don't survive an HA restart. So before the bus comes up
the dashboard shows **"entity not found"** and there's no history to browse.

The bootstrap seeds each known entity as `unavailable` on startup so the cards
render and history stays continuous. It skips entities that already exist, so it
never overwrites a live value the add-on has pushed. When the bus comes up the
add-on's real values take over; when it goes quiet the last value stays (the
add-on's job), and a later HA restart re-seeds the placeholders.

## Regenerate the script

After changing the decoder sensor tables (`EZ_SENSORS` / `BG_SENSORS`):

```
python CANbus_data/tools/gen_ha_bootstrap.py
```

## Deploy

1. Packages must be enabled in `configuration.yaml` (already are on the Pi):
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Copy the files onto the Pi:
   - `packages/canbus_bootstrap.yaml` → `/config/packages/canbus_bootstrap.yaml`
   - `packages/canbus_controls.yaml` → `/config/packages/canbus_controls.yaml`
     (or let `python CANbus_data/tools/deploy_addon.py --with-packages` do it)
   - `python_scripts/bootstrap_canbus_entities.py` → `/config/python_scripts/bootstrap_canbus_entities.py`
3. **Check configuration** (Developer Tools → YAML, or `ha core check`), then
   **restart Home Assistant** — enabling `python_script` needs a restart. The
   automation then fires on the start event and creates the placeholders.

Editing only the script later (not adding/removing the integration) just needs
**Developer Tools → YAML → Reload → Python Scripts**, no restart.
