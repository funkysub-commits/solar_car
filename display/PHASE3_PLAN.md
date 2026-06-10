# Phase 3 plan — e-ink display refactor (`display/addon/display.py`)

Status: **planned, not started** (written 2026-06-09). Do Phase 2's Pi
verification first (see `PI_TODO.md`) so this lands on a known-good base.

## Goal

Split the 965-line `display.py` monolith into focused modules and fix its
known defects, with **zero visual or behavioral change** otherwise — the
panel must render pixel-identically before and after, proven by a
golden-image test (see Verification).

## Why it's safe to split

The add-on builds with `display/addon/` as its Docker context and copies
files flat into `/` (`COPY run.sh display.py logo.png /`). New modules just
live next to `display.py` and the COPY line becomes `COPY run.sh *.py
logo.png /`. No vendoring is needed (unlike the CANbus add-on) because
nothing here is shared with other components — the display add-on stays
self-contained.

The Waveshare driver import is already guarded (`display.py:66-73`) so all
rendering code imports and runs headless on a PC; only `main()` touches the
panel hardware.

## Module breakdown

| New module | Pulls from display.py | Responsibility |
| --- | --- | --- |
| `config.py` | ~lines 120–260 | All env/option parsing (HA_URL, entity IDs, units, poll rates), validated and in one place. |
| `layout.py` | constants scattered through drawing code | Named geometry: panel `W,H = 800,480`, `DIV_X = 452`, `BAT_DIV_Y = 262`, `CONTENT_BOT = 432`, speedometer `cx,cy,r = 228,206,120`, temp-bar coordinates, font loading. Every magic pixel number gets a name here; the draw functions reference them. |
| `ha_client.py` | ~lines 279–373 | REST access: `ha_get`, `ha_post_state`, `ha_call_service`, the typed readers (`read_number`, `read_temp_c`, `read_message`, `read_hidden`, `set_hidden`), `entity_age_seconds`. The repeated try/request/except boilerplate collapses into one helper. |
| `warnings.py` | ~lines 404–451 + `sync_hidden` from main | `compute_stale`, `build_warnings`, priority scheme (CAN-bus 100 > temps 70–95 > sensor-stale 50 > user message 30), hidden-key sync. |
| `render.py` | ~lines 457–666 | The `draw_*` functions (speedometer, battery, temps, notify toast, header) and `render()` composition. Pure PIL — fully testable on PC. |
| `panel.py` | ~lines 672–719 + refresh state from main | E-ink refresh state machine: `region_snaps` change detection, `push_region` partial refresh, periodic `full_refresh` de-ghosting, `settle_and_sleep` deep-sleep, wake-on-change. |
| `units.py` | small | `clamp`, C↔F conversion, rpm→speed conversion — currently copy-pasted between `display.py`, `bms_gui.py`, and `simulator/solar_sim.py`. The other two copies stay put for now (different deployment units); dedupe inside the add-on only. |
| `display.py` (kept) | ~lines 753–962, slimmed | `main()` only: init, signal handling, poll cadence, power-toggle handling, assemble-and-push loop. Target well under 200 lines. |

## Bug fixes bundled in (each verified in the code, line refs current)

1. **Hidden-keys truncation** (`display.py:372`): `set_hidden` writes
   `",".join(keys)[:255]` — HA's input_text cap, but a silent mid-key cut
   corrupts the list when many warnings are hidden. Fix: drop
   lowest-priority keys until the string fits, log what was dropped.
2. **`patch.py` silent no-op**: it rewrites the Waveshare `epdconfig.py` by
   string replacement with no check that the patterns matched — if Waveshare
   restructures the file, the build "succeeds" and the add-on crashes at
   runtime. Fix: assert every replacement hit, so the Docker build fails
   loudly instead.
3. **Font fallback** (`_font`, ~line 226): silently falls back to PIL's
   default font; the dashboard renders wrong with no log. Fix: warn once.
4. **HA-unreachable visibility**: every fetch failure is `logging.debug` —
   a dead Supervisor proxy looks identical to healthy idle in the logs at
   INFO level. Fix: rate-limited WARNING (e.g., once per minute) while
   unreachable, plus distinguish "HA unreachable" from "CAN sensors stale"
   in the staleness logic — today failed reads leave `last_iso` frozen, so
   an HA outage eventually shows as "CAN bus not connected", which sends the
   reader to the wrong subsystem. (Confirm the exact behavior while
   implementing; the toast text may deserve its own warning key.)

## Explicitly NOT changing

- Panel layout, fonts, geometry — pixel-identical output is the acceptance
  bar.
- The power-toggle default (`powered = state != "off"` → defaults ON when
  the helper is absent). The review flagged it as a bug; it is intentional
  (`display.py:848` comment + README §5.2 note).
- Entity-ID defaults, the add-on options schema, the refresh strategy
  (fast/partial/full cadence, idle deep-sleep), `sensor.eink_warnings`
  publishing.
- `bms_gui.py` and the simulator (Phase 4 decides their fate; the BLE GUI
  is fallback-only).

## Verification

**On the PC (before touching the Pi):**
1. *Golden-image harness first, refactor second* — same pattern as Phase 2:
   with the **current** `display.py`, write a small harness that stubs the
   env/HA reads, drives `render()` across a matrix of synthetic states
   (normal driving, each warning type active, stale CAN, message set, hidden
   warnings, temp unit F, speed unit kmh/rpm, SoC boundaries 0/15/100), and
   saves each frame as PNG + SHA256. Commit the hashes (and a few PNGs for
   eyeballing) under `display/tests/golden/`.
2. Refactor, then re-run the harness through the new modules: every hash
   must match byte-for-byte. Bug-fix #1 and #4 change behavior outside
   `render()`, so they don't disturb the image hashes.
3. Unit tests for `warnings.py` (priority ordering, hide/unhide round-trip,
   the 255-char fitting logic) and `ha_client.py` (parsing, staleness math)
   with mocked `requests`.

**On the Pi (when next powered, after the add-on copy):**
4. Bump `display/addon/config.yaml` version, copy to `/addons/solar_epaper/`,
   rebuild, and drive it with `simulator/solar_sim.py` — confirm partial
   refresh still flash-free, full refresh cadence unchanged, deep-sleep
   after `idle_sleep`, wake on change, power toggle clears the screen.
   (The `.webcam/` scratch-dir convention from earlier verification rounds
   works for before/after screen photos.)

## Suggested commit sequence

1. Golden harness + committed hashes against the *unrefactored* code.
2. Mechanical split into modules, no behavior change — hashes prove it.
3. Bug fixes #1–#4, each with its test.
4. Docs: update README §5 file references, add-on version bump.

## Rollback

Each step is one commit; the add-on is versioned. If the Pi rebuild
misbehaves, reinstall the previous add-on version from the old commit —
the canbus add-on and HA config are untouched by this phase.
