#!/usr/bin/env python3
"""
Solar Car telemetry simulator.

Pushes realistic, continuously-changing dummy data to Home Assistant so the
e-ink dashboard (and HA dashboards) can be demonstrated without the real CAN
bus / BLE BMS hardware connected.

It writes to the *real* integration entities - the EZkontrol CAN reader
(ezkontrol_*) and the bestgo BLE BMS (bestgo_*) - so the display and dashboard
read exactly the entities they will use with real hardware. When the hardware
is connected those integrations take over and this simulator should be stopped.

It models a driving cycle - acceleration, cruising, braking and stops - with
motor / controller / battery temperatures that respond to load, a state of
charge that slowly drains and "recharges", and a pack voltage that tracks SoC.

Motor speed is simulated as realistic wheel rpm (a direct-drive hub motor), so
the dashboard's rpm -> mph conversion produces sensible road speeds. The
Raspberry Pi temperature is NOT simulated - that sensor is real.
"""
import os
import time
import random
import logging

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

HA_URL = os.environ.get("HA_URL", "http://10.126.155.163:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
TICK = float(os.environ.get("TICK", "1.0"))          # seconds between pushes

HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

RPM_MAX = 600.0           # motor/wheel rpm full-scale (~35 mph on a 20" wheel)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def push(entity, state, friendly, unit):
    """POST a simulated state to Home Assistant's REST API."""
    try:
        requests.post(
            f"{HA_URL}/api/states/{entity}",
            headers=HEADERS, timeout=5,
            json={"state": str(state),
                  "attributes": {"friendly_name": friendly,
                                 "unit_of_measurement": unit}},
        )
    except Exception as e:
        logging.warning(f"push {entity} failed: {e}")


def main():
    if not HA_TOKEN:
        logging.warning("HA_TOKEN is empty - all pushes will fail")

    speed = 0.0          # current motor/wheel speed (rpm)
    target = 0.0         # speed the drive cycle is currently aiming for
    hold = 0             # ticks remaining before a new target is chosen
    motor_t = 26.0       # motor temperature (degrees C)
    ctrl_t = 25.0        # controller temperature
    batt_t = 24.0        # battery pack temperature
    soc = 87.0           # state of charge (%)

    logging.info(f"simulator started -> {HA_URL}  (tick {TICK}s)")

    while True:
        # --- drive cycle: choose a new target speed when the hold expires ---
        if hold <= 0:
            if random.random() < 0.25:
                target = 0.0                          # come to a full stop
            else:
                target = random.uniform(120, 560)     # cruise somewhere
            hold = random.randint(6, 20)              # hold it 6-20 s
        hold -= 1

        # --- move toward the target with limited accel / decel ---
        step = 45.0 if target > speed else 80.0       # brakes beat the motor
        if abs(target - speed) <= step:
            speed = target
        else:
            speed += step if target > speed else -step
        speed = clamp(speed, 0.0, RPM_MAX)

        load = speed / RPM_MAX                        # 0..1 thermal/electrical load

        # --- thermal models: heat with load, cool toward ambient ---
        motor_t += 1.80 * load - 0.060 * (motor_t - 25.0)
        ctrl_t  += 1.10 * load - 0.050 * (ctrl_t - 24.0)
        batt_t  += 0.20 * load - 0.020 * (batt_t - 22.0)
        motor_t = clamp(motor_t, 20.0, 95.0)
        ctrl_t  = clamp(ctrl_t, 20.0, 90.0)
        batt_t  = clamp(batt_t, 18.0, 60.0)

        # --- state of charge drains with use; loop the demo when low ---
        soc -= (0.004 + 0.020 * load)
        if soc < 15.0:
            soc = 95.0
            logging.info("battery 'recharged' - looping demo")

        # pack voltage tracks SoC (LiFePO4, ~45-57 V) and sags a little under load
        voltage = 45.0 + 12.0 * (soc / 100.0) - 1.5 * load

        # small reading jitter so the speedometer is never perfectly still
        speed_out = round(clamp(speed + random.uniform(-8, 8), 0.0, RPM_MAX))

        # EZkontrol CAN reader entities
        push("sensor.ezkontrol_motor_speed", speed_out, "EZkontrol Motor Speed", "rpm")
        push("sensor.ezkontrol_motor_temp", round(motor_t, 1), "EZkontrol Motor Temp", "°C")
        push("sensor.ezkontrol_controller_temp", round(ctrl_t, 1), "EZkontrol Controller Temp", "°C")
        # bestgo BLE BMS entities
        push("sensor.bestgo_soc", round(soc, 1), "bestgo SoC", "%")
        push("sensor.bestgo_pack_temp", round(batt_t, 1), "bestgo Pack Temp", "°C")
        push("sensor.bestgo_pack_voltage", round(voltage, 2), "bestgo Pack Voltage", "V")

        logging.info(f"speed={speed_out:>3} rpm  motor={motor_t:4.1f}  "
                      f"ctrl={ctrl_t:4.1f}  batt={batt_t:4.1f}  "
                      f"soc={soc:4.1f}%  v={voltage:4.1f}")
        time.sleep(TICK)


if __name__ == "__main__":
    main()
