# BESTGO battery — standalone Docker test

A minimal Docker container to confirm the BESTGO battery's CAN frames
decode correctly, run directly on the HA OS box — **not** a Home Assistant
add-on, and it touches no HA entities. It just brings up `can0`, decodes
the BESTGO BMS frames, and prints the values.

## Prerequisites on the HA OS box

- The **SSH & Web Terminal add-on** with **Protection Mode OFF** (so the
  shell can reach Docker). The shell logs in as `hassio`, so Docker
  commands need `sudo` (`sudo docker ...`).
- The **SH-C31G** USB-CAN adapter plugged into the Pi and running its
  gs_usb firmware — it must enumerate as USB `1d50:606f` and produce a
  `can0` interface. If `lsusb`/`/sys` shows `0483:df11` it is stuck in
  STM32 DFU mode; replug it (no BOOT/DFU jumper).

## Build

Push this folder to the box (e.g. to `/share/bestgo-test`), then:

```sh
sudo docker build -t bestgo-test /share/bestgo-test
```

## Run

Against the real bus — needs host networking (to see `can0`) and
`NET_ADMIN` (to bring it up). The entrypoint sets `can0` to 500 kbps:

```sh
sudo docker run --rm --network host --cap-add NET_ADMIN bestgo-test 30
```

Without hardware — synthetic frames (decodes a captured real frame set):

```sh
sudo docker run --rm bestgo-test -dummy 12
```

The argument is the run duration in seconds (0 = until Ctrl+C).
`CAN_CHANNEL` / `CAN_BITRATE` env vars override the interface and rate.

## Expected output

```
BESTGO decode test -- SocketCAN can0
  battery: Lithium Valley  mfr=LVaiiey  fw=v1.1  capacity=56 Ah
  t=   2s  frames=28  ids=10/10  V=52.80  I=+0.0A  SOC=56%  T=22.0C  ...
  ...
done -- N frames, 10/10 BESTGO IDs seen
```
