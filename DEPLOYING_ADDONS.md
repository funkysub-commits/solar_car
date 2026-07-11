# Deploying the Home Assistant add-ons

This repo ships two **local** Home Assistant add-ons that run on the car's Raspberry Pi
(Home Assistant OS). This is the practical guide to building and pushing changes to them,
and the traps that have actually bitten us.

| | E-Ink Display | CANbus Reader |
|---|---|---|
| Source folder | `display/addon/` | `CANbus_data/ha_addons/solar-car-canbus/` |
| Add-on name | Solar Car E-Ink Display | Solar Car CANbus Reader |
| Config slug | `solar_epaper` | `solarcar_canbus` |
| **Installed** slug (API/CLI) | `local_solar_epaper` | `local_solarcar_canbus` |
| On-Pi source dir | `/addons/solar-epaper/` | `/addons/solar-car-canbus/` |
| Needs | GPIO/SPI (`full_access`) | `NET_ADMIN`, USB (`can0`) |
| Deploy script | `scratchpad deploy_eink.py`* | `CANbus_data/tools/deploy_addon.py` |

\* The e-ink deploy script has lived in a scratchpad; the steps below are the durable
version. Both add-ons deploy the same way.

The Pi's address and SSH/HA credentials are in `status.json` at the repo root
(git-ignored). The Pi is on DHCP, so **its IP changes** — always read it from `status.json`
rather than hard-coding. It is currently reachable over SSH as user `hassio`.

---

## Mental model: how a local add-on builds

1. A local add-on is just a folder under `/addons/` on the Pi containing `config.yaml`,
   a `Dockerfile`, and the code.
2. The Supervisor builds a **Docker image** from that folder. Both our Dockerfiles
   **`COPY` the source into the image** — the code is *baked in at build time*, not
   mounted live. **So editing files on the Pi does nothing until the image is rebuilt.**
3. The Supervisor decides "is there a newer version to build?" by comparing the
   **installed** version against the **`version:` in `config.yaml`**. So a code change with
   no `version:` bump is invisible to `Update`.

**Therefore every deploy is: get the new files onto the Pi → tell the Supervisor to
re-read the folder → rebuild → (re)start.** And **every code change needs a `version:`
bump**, or the rebuild won't pick it up.

---

## The deploy, step by step

### 1. Bump the version

Edit `config.yaml` → `version:`. Any increase works (`1.8.0` → `1.8.1`). Commit it.

### 2. Get the files onto the Pi

The Advanced SSH add-on here has **no SFTP subsystem**, and the login user can't write
`/addons` directly, so we stream a gzip tar over the SSH exec channel and extract it with
`sudo`. The deploy scripts do this; the essence is:

```bash
# on your machine: tar the add-on folder (excluding __pycache__/.git),
# then pipe it over ssh and extract as root
tar czf - -C display/addon . | ssh hassio@<PI_IP> \
  'sudo tee /addons/solar-epaper/.d.tgz >/dev/null &&
   sudo tar xzf /addons/solar-epaper/.d.tgz -C /addons/solar-epaper &&
   sudo rm /addons/solar-epaper/.d.tgz'
```

The Python deploy scripts also **back up** the existing folder to
`/addons/<name>.bak.<timestamp>` first, and normalise text files to LF (see gotchas).

> Alternatives if you prefer: install the **Samba** add-on and copy into the `addons`
> share, or use **Studio Code Server** to edit in place. Either way you still rebuild.

### 3. Reload, rebuild, verify

Over SSH on the Pi, using the `ha` CLI:

```bash
ha addons reload                       # re-scan /addons for changed config.yaml
ha addons update local_solar_epaper    # rebuild to the new version
ha addons info  local_solar_epaper     # check: version, state=started, boot
ha addons logs  local_solar_epaper     # confirm it's actually doing its job
```

Equivalent Supervisor REST calls (what the scripts use — `$SUPERVISOR_TOKEN` is present in
any add-on/SSH shell):

```bash
curl -sX POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/store/reload
curl -sX POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/addons/local_solar_epaper/update
curl -s      -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/addons/local_solar_epaper/info
```

If `version:` didn't change, `update` no-ops — use `ha addons rebuild <slug>` to force a
rebuild at the same version.

**Always read the logs.** `state=started` only means the container is up, not that the
job works — the e-ink panel can fail on GPIO, the CAN reader can fail to open `can0`.

---

## Options and schema changes — the #1 trap

Add-on **options** (the values under `options:` in `config.yaml`) are saved *per-install*
in the Supervisor, **separately from the code**. This causes two recurring problems:

### A saved option OVERRIDES a changed default — silently

If you change a default in `config.yaml` (say `speed_poll: 2.5` → `1`), an add-on that was
**already installed keeps its saved `2.5`**. The new default only applies to a fresh
install. We hit this twice (`speed_poll`, `full_refresh_every`) — the code shipped but the
behaviour didn't change until we set the option explicitly.

**Fix:** after deploying, set the option explicitly (UI: *Settings → Add-ons → … →
Configuration*, or POST the options — see below), then restart.

### Adding a NEW option to the schema

If you add a key to both `options:` and `schema:`, an already-installed add-on's saved
options **don't have it**, and the add-on can **fail schema validation on start**. You must
re-POST the saved options with the new key merged in. The e-ink deploy script does this
automatically (reads `config.yaml` defaults, merges any missing keys, POSTs back), preserving
existing values like `temp_unit: F`.

### POSTing options — beware the silent failure

Posting options with a shell-quoted JSON `-d '...'` payload **can fail while returning an
empty body that looks like success**, leaving the option unchanged. Post from a **file**
instead:

```bash
# write the full options JSON to /tmp/opts.json on the Pi, then:
curl -sX POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
     -H "Content-Type: application/json" --data @/tmp/opts.json \
     http://supervisor/addons/local_solar_epaper/options
# expect: {"result":"ok",...}  then restart the add-on
```

Always re-read `info` afterwards and confirm the value actually took.

---

## Protection Mode

**Protection Mode resets to ON on every rebuild/update.** It blocks elevated container
capabilities, and the Supervisor API **forbids an add-on turning off its own protection**
(403) — so it must be toggled in the **UI**: *Settings → Add-ons → <add-on> → toggle
"Protection mode" off*, then start.

- **E-Ink** uses `full_access` for GPIO/SPI. With protection **ON it cannot claim the
  panel** — you'll see the driver import fail / `state: error` / EPERM. **Turn protection
  off** after a rebuild that flips it back on. (Some recent deploys came back with it
  already off and auto-started — always check `info`.)
- **CANbus** uses `NET_ADMIN` + USB. In practice it has **rebuilt and run with protection
  ON** — `can0` came up fine. Check the logs; only toggle if it can't open the bus.

---

## Gotchas checklist

- [ ] **Bumped `version:`?** No bump → rebuild is a no-op.
- [ ] **Read the logs**, not just `state`. Started ≠ working.
- [ ] **Saved options** may override new defaults — set them explicitly and re-check `info`.
- [ ] **New schema key** → merge it into saved options or start fails validation.
- [ ] **Options POST** can fail silently — post from a file, then verify.
- [ ] **Protection Mode** flipped back on — toggle off in the UI if the add-on needs
      hardware (always for e-ink).
- [ ] **Line endings:** `run.sh` and other text files must be **LF**, not CRLF. Editing on
      Windows can introduce CRLF; a CRLF `run.sh` fails with a `bad interpreter` / `no such
      file` error. The deploy scripts convert text files to LF on upload — if you copy files
      another way, run `dos2unix` or save as LF.
- [ ] **Pi IP** comes from `status.json` (DHCP; it moves).
- [ ] A **backup** of the previous folder is left at `/addons/<name>.bak.<timestamp>` —
      handy for rollback, worth pruning occasionally.

---

## E-Ink specifics

- **Golden tests are the safety net.** Before/after a render change:
  ```bash
  cd display && python tests/golden_harness.py check      # must pass
  python tests/golden_harness.py write                    # regenerate after an intended change
  python -m unittest discover -s tests -p 'test_*.py'
  ```
  Hashes depend on the committed DejaVu fonts in `display/tests/fonts/` and the Pillow
  version — regenerate on the same machine you verify on.
- **Partial-refresh region x-coordinates must be multiples of 8.** The panel only refreshes
  byte-aligned columns. The panel is also mounted upside-down, so every buffer is rotated
  180° at the push layer (`panel.py`) — a region `(x0..x1)` maps to `(W-x1 .. W-x0)`, which
  stays 8-aligned only because `W=800` and both bounds are multiples of 8. **Break that and
  partial refreshes smear on the real panel — the golden tests won't catch it** (they hash
  the un-flipped frame). Verify new region math by hand.
- The clock ticks every second only while the display is on (`clock_tick` option); it does
  **not** wake the panel from idle sleep on its own.

## CANbus specifics

- The frame decoders live in `CANbus_data/solarcar_can/` (the **source of truth**) and are
  **vendored** into `CANbus_data/ha_addons/solar-car-canbus/solarcar_can/` because HA builds
  the add-on with that folder as the Docker context — the package has to be *inside* it.
  **Edit the source copy, then re-sync** with `python CANbus_data/sync_addon.py` (the
  vendored files carry a `# GENERATED … DO NOT EDIT` header). The deploy ships whatever is in
  the add-on folder, so an un-synced edit to the source alone won't reach the Pi.
  Editing `can_reader.py` (which is not part of that package) needs no re-sync.
- `can0` is brought up in `run.sh` (`ip link set can0 ...`); a rebuild that can't find the
  adapter will log `can0` errors and report `canadapter_status=0`.
- Per-device push intervals and dummy-mode flags are add-on options
  (`ezkontrol_push_interval`, `bestgo_dummy`, …).

---

## Rollback

The previous folder is backed up on-Pi before each upload:

```bash
ssh hassio@<PI_IP> 'ls -d /addons/solar-epaper.bak.* '     # find the timestamped backup
# restore it:
ssh hassio@<PI_IP> 'sudo rm -rf /addons/solar-epaper && sudo mv /addons/solar-epaper.bak.<ts> /addons/solar-epaper'
ha addons reload && ha addons rebuild local_solar_epaper
```

Or just check out the previous commit, bump the version, and redeploy.
