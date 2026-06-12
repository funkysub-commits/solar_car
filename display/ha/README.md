# E-ink notification / message system — Home Assistant setup

The add-on ([../addon/display.py](../addon/display.py)) now shows **all** warnings as a
single notification "toast" at the bottom-centre of the e-paper, and publishes the
live message list back to Home Assistant so you can see every message and hide
individual ones from the screen.

## What the add-on does

* Draws a small centred notification box at the bottom of the panel **only when a
  warning is active**. It shows the single most important warning; if more than one
  is active a round badge shows the total count.
* Warning priority (most important first): **CAN bus not connected** → **high temp
  (hotter = higher)** → **a sensor that stopped updating** → **user message**.
* Any value whose source entity has stopped updating (its `last_reported` stops
  advancing) gets a small `!` mark drawn next to it. A value that is simply steady
  but still being reported is **not** marked.
* Publishes `sensor.eink_warnings` (state = active message count) with attributes
  `warnings`, `lines`, `keys_visible`, `keys_hidden`.
* Reads `input_text.eink_hidden` (comma-separated warning keys) and removes those
  from the e-paper. Keys whose warning is no longer active are pruned automatically,
  so a warning that clears and later returns shows again.

## Apply (two pieces)

### 1. Helper + hide scripts — `eink_messages.yaml`

This defines `input_text.eink_hidden` and `script.eink_hide` / `eink_unhide` /
`eink_unhide_all`.

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

> **User message helper:** the add-on shows the free-text message from
> `input_text.eink_message`. If you don't already have that helper, create it
> (UI text helper, max 255) or uncomment the `eink_message:` block in
> `eink_messages.yaml`. Without it the user-message line simply never appears
> (everything else still works).

### 2. Dashboard section — `dashboard_messages_section.yaml`

This adds an **E-Paper Messages** section that lists every active message (live text,
hidden ones marked) with a **Hide** button for each message currently on the e-paper,
plus an **Unhide all** button.

1. Open the Solar Car dashboard → 3-dot menu → **Edit dashboard** → 3-dot menu →
   **Raw configuration editor**.
2. Paste the `type: grid` block from
   [dashboard_messages_section.yaml](dashboard_messages_section.yaml) as a new entry in
   the view's `sections:` list.
3. **Save**.

The Hide buttons only appear for messages that are actually on the e-paper right now,
so the section stays uncluttered.

## Warning keys (for reference)

| key | shown when |
|-----|------------|
| `can` | the CAN bus/adapter is down — from `sensor.canadapter_status` (1/0), else inferred from every CAN-fed sensor being stale → "CAN bus not connected" |
| `can_batt` | bus is up but the battery isn't on CAN — from `sensor.bestgo_status`, else inference; marks `!` on exactly the battery-fed values (SoC, voltage, BATT temp) |
| `can_ezk` | bus is up but the EZkontrol isn't on CAN — from `sensor.ezkontrol_status`, else inference; marks `!` on exactly the EZkontrol-fed values (speed, motor temp, EZK temp) |
| `ha` | the add-on can't reach Home Assistant at all → "Home Assistant unreachable" (replaces the CAN/staleness deductions, which are unknowable during an HA outage) |
| `temp_t_motor` / `temp_t_ezk` / `temp_t_batt` / `temp_t_pi` | that temperature ≥ `temp_warn` (live reading only) |
| `stale_speed` / `stale_t_motor` / `stale_t_ezk` / `stale_t_batt` / `stale_t_pi` / `stale_soc` / `stale_voltage` | that sensor stopped updating (but the bus as a whole is alive) |
| `user` | `input_text.eink_message` is non-empty |

To hide a message manually you can also just type its key into
`input_text.eink_hidden` (comma-separated); to unhide, remove it or clear the field.
