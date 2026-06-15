# Solar Car CANbus Reader — Home Assistant add-on

Reads the EZkontrol motor controller and the BESTGO battery off the shared
CAN bus and pushes sensors to Home Assistant. On the Pi this lives at
`/addons/solar-car-canbus/`.

> **Do not edit `solarcar_can/` in this folder — it is a vendored copy.**
> The real source is `CANbus_data/solarcar_can/`. After changing it, run
> `python CANbus_data/sync_addon.py` to refresh the copy here (each vendored
> file also carries a "GENERATED — do not edit" header).

Build/deploy steps and the sensor list are in
[`../../README.md`](../../README.md) (CAN bus) and the
[top-level README](../../../README.md) §6 (full system + sensors).
