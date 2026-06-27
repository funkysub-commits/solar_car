> Response from another LLM to the prompt in `DEBUG-prompt-for-llm.md`
> (received 2026-06-27). Saved verbatim. Live state + our own findings:
> `DEBUG-pi-rx-slcan-20260624.md`.

# USB-CAN Receive Failure on Raspberry Pi 4 / HAOS

## Executive Summary

The evidence is most consistent with a **Pi 4 VL805/xHCI/HAOS-kernel receive-side USB completion problem**, not a CAN, termination, bitrate, python-can, permissions, or ordinary CDC-ACM issue.

The planned rollback to **HAOS 15 / kernel 6.6.74** is a sound next step. The `gs_usb` observation — where the adapter appears to receive and ACK CAN frames at the hardware/protocol level but Linux `rx_packets` stays at zero — strongly points to a failure in the adapter-firmware-to-host-software delivery path.

One important correction: in CDC-ACM, the `V` command round-trip is not evidence that only USB control transfers work. CDC-ACM serial payload normally travels over USB bulk endpoints. So the Pi can receive at least some bulk-IN data. What appears to fail is sustained/asynchronous streaming after the adapter is opened.

---

## Best-Ranked Root-Cause Hypotheses

### 1. VL805/xHCI transfer-ring bug or missing/ineffective quirk — highest probability

This is the cleanest fit for the cross-driver symptom. Both `gs_usb` and `cdc_acm` depend on the Pi 4's VIA VL805 xHCI host controller and its DMA/transfer-ring handling.

A particularly relevant failure class is a VL805/xHCI transfer-ring issue where the controller may prefetch beyond the end of a transfer-ring segment. If stale prefetched TRBs are later used, an endpoint can effectively stay idle even though the device is configured and no obvious disconnect occurs.

This maps well to the observed behavior:

- Endpoint appears configured.
- No disconnect/reset is logged.
- TX and request/response activity can work.
- The IN endpoint that should deliver received CAN frames appears to go quiet.

#### Commands to collect evidence

```bash
uname -a
cat /proc/cmdline
dmesg -T | grep -Ei 'xhci|vl805|trb|transfer event|deq|endpoint|usb'
lsusb -t
lsusb -vv -d 16d0:117e
lsusb -vv -d 1d50:606f
```

#### Red-flag dmesg signatures

```text
xhci_hcd ... Transfer event TRB DMA ptr not part of current TD
xhci_hcd ... ERROR Transfer event for disabled endpoint
xhci_hcd ... WARN Set TR Deq Ptr cmd failed
xhci_hcd ... failed to queue trbs
xhci_hcd ... host not responding to stop endpoint command
xhci_hcd ... HC died
```

Absence of these messages does **not** clear VL805. A ring/endpoint idle condition can be silent if the xHC simply never completes queued IN URBs.

---

### 2. VL805 firmware / internal USB-2 hub / transaction-translator interaction — high probability

The adapter is on Bus 001 behind the Pi 4's internal VIA USB-2 hub `2109:3431`. The black USB-2 ports and blue USB-3 ports all traverse the same VL805 host path.

If the CANable2/STM32 presents as full-speed USB, that is especially interesting because full-speed devices behind a high-speed hub involve split transactions. A powered external USB-2 hub can change the transaction translator/topology and sometimes avoid host-controller/internal-hub corner cases.

#### Commands

```bash
lsusb -t
lsusb -vv -d 16d0:117e | egrep -i 'bcdUSB|bDeviceClass|bMaxPacketSize0|bNumConfigurations|Endpoint|wMaxPacketSize|bInterval|Transfer Type'
lsusb -vv -d 1d50:606f | egrep -i 'bcdUSB|bDeviceClass|bMaxPacketSize0|Endpoint|wMaxPacketSize|bInterval|Transfer Type'
```

#### Look for

```text
12M
full-speed
bulk IN endpoint with wMaxPacketSize 64
bulk OUT endpoint with wMaxPacketSize 64
interrupt IN endpoint for CDC notification
```

#### Topologies to test

```text
A. Direct Pi USB-2 black port
B. Direct Pi USB-3 blue port
C. Powered external USB-2 hub into Pi USB-2
D. Powered external USB-2 hub into Pi USB-3
E. Different powered hub chipset, if available
```

If any external hub makes receive work, treat the root cause as VL805/internal-hub/topology-specific even if the exact kernel bug remains unidentified.

---

### 3. HAOS/Raspberry Pi kernel regression or downstream config issue — high probability

The kernel correlation is meaningful:

- Fails on `6.12.47-haos-raspi`.
- Fails on `6.18.33-haos-raspi`.
- Reportedly worked on an earlier setup likely based on the 6.6 LTS line.

The correct A/B test is:

```text
same Pi
same adapter
same firmware
same battery
same wiring
same smoke test
only kernel/HAOS changes
```

#### Smoke test: gs_usb mode

```bash
ip -details -statistics link show can0
ip link set can0 down || true
ip link set can0 up type can bitrate 500000
timeout 10 candump -tz can0
ip -details -statistics link show can0
cat /sys/class/net/can0/statistics/rx_packets
cat /sys/class/net/can0/statistics/rx_errors
```

#### Smoke test: slcan raw mode

```bash
python3 - <<'PY'
import serial, time, os
p="/dev/ttyACM0"
s=serial.Serial(p, 115200, timeout=0.2)
for cmd in [b'C\r', b'S6\r', b'O\r']:
    s.write(cmd)
    time.sleep(0.1)
print("opened")
t=time.time()+6
n=0
buf=b""
while time.time()<t:
    b=s.read(4096)
    if b:
        n += len(b)
        buf += b[:200]
print("bytes", n)
print("sample", repr(buf[:500]))
s.write(b'C\r')
s.close()
PY
```

#### Interpretation matrix

| Result | Meaning |
|---|---|
| HAOS 15 / 6.6 works, HAOS 18 / 6.18 fails | Very strong kernel/VL805 regression evidence |
| Both fail | Look harder at Pi hardware, VL805 firmware/EEPROM, topology, or adapter firmware interaction |
| slcan works but gs_usb fails | Driver-specific, likely `gs_usb`/SocketCAN path |
| gs_usb works but slcan fails | CDC/tty/slcan-specific |
| external hub fixes both | VL805/internal-hub/topology/TT issue likely |

---

### 4. CANable2 firmware / Linux-host interaction — medium probability

Because both firmwares fail, this is less likely than a VL805/kernel issue. But it is still possible that both firmware modes share a USB-device-stack behavior that interacts badly with the Pi host.

Possible shared issues:

- STM32 USB stack behavior.
- Endpoint sizes.
- ZLP behavior.
- NAK behavior.
- Receive-queue flushing behavior.
- Host-specific timing assumptions.

The key diagnostic is `usbmon`.

---

### 5. CDC-ACM termios/flow-control/line-discipline issue — low probability

This can cause ordinary tty receive confusion, but the raw pyserial result plus the `gs_usb` failure make it unlikely.

Still, this is a cheap completeness test.

```bash
stty -F /dev/ttyACM0 raw -echo -echoe -echok -echoctl -echoke \
  -icanon -isig -iexten -ixon -ixoff -crtscts min 0 time 1 115200

python3 - <<'PY'
import os, time
fd=os.open('/dev/ttyACM0', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
for c in [b'C\r', b'S6\r', b'O\r']:
    os.write(fd,c); time.sleep(.2)
t=time.time()+6
n=0
while time.time()<t:
    try:
        b=os.read(fd,4096)
        if b:
            n += len(b)
            print(repr(b[:200]))
    except BlockingIOError:
        time.sleep(.02)
print("total", n)
os.write(fd,b'C\r')
os.close(fd)
PY
```

If this receives data but python-can does not, the problem moves back up to python-can. Based on the existing raw-read result, this is not expected.

---

### 6. Power/EMI/undervoltage — low probability but cheap to exclude

This is already mostly ruled out:

- No undervoltage.
- No disconnects.
- No resets.
- Laptop works on the same bus.

Still, a powered hub is useful because it changes more than power: hub silicon, TT behavior, cable drive, and sometimes xHCI scheduling.

#### Check logs

```bash
dmesg -T | grep -Ei 'under-voltage|voltage|reset high-speed|reset full-speed|disconnect|error -71|error -110|error -32|device descriptor|unable to enumerate|HC died'
```

---

## Prioritized Diagnostic Plan

### Phase 1 — Establish immutable baseline

Run on failing HAOS 18:

```bash
uname -a
cat /proc/cmdline
cat /etc/os-release 2>/dev/null || true
dmesg -T | grep -Ei 'usb|xhci|vl805|cdc_acm|gs_usb|can|ttyACM|under-voltage|voltage' > /config/usb-can-dmesg-baseline.txt
lsusb > /config/lsusb.txt
lsusb -t > /config/lsusb-tree.txt
lsusb -vv -d 16d0:117e > /config/lsusb-slcan-16d0-117e.txt
lsusb -vv -d 1d50:606f > /config/lsusb-gsusb-1d50-606f.txt
```

This proves:

- Exact kernel and cmdline.
- Whether the device is 12M/full-speed or 480M/high-speed.
- Endpoint packet sizes and endpoint numbers.
- Whether CDC has the expected interrupt notification plus bulk data endpoints.
- Whether gs_usb has expected bulk endpoints.

---

### Phase 2 — Capture usbmon during failure

Start a privileged container:

```bash
docker run --rm -it --privileged \
  -v /dev:/dev \
  -v /sys:/sys \
  -v /run/udev:/run/udev \
  -v /config:/config \
  debian:bookworm bash
```

Inside the container:

```bash
apt-get update
apt-get install -y usbutils tcpdump iproute2 kmod python3 python3-pip can-utils procps
modprobe usbmon || true
mount -t debugfs none /sys/kernel/debug || true
ls /sys/kernel/debug/usb/usbmon
lsusb -t
```

Assuming the adapter is on Bus 001:

```bash
tcpdump -i usbmon1 -w /config/canable-slcan-usbmon.pcap
```

In another shell/container, run:

```bash
python3 - <<'PY'
import serial, time
s=serial.Serial('/dev/ttyACM0',115200,timeout=.2)
for c in [b'V\r', b'C\r', b'S6\r', b'O\r']:
    s.write(c); time.sleep(.2); print(c, repr(s.read(200)))
time.sleep(6)
print("post", repr(s.read(1000)))
s.write(b'C\r')
PY
```

#### How to interpret usbmon

| usbmon observation | Interpretation |
|---|---|
| Bulk-IN URBs are submitted and complete with payload for `V`, then after `O` only submissions, no completions | endpoint/device/xHCI stream stall |
| Bulk-IN completions arrive with `status 0` and nonzero length but userland sees zero | cdc_acm/tty buffering or container/device pass-through issue |
| Bulk-IN completions show errors like `-71`, `-75`, `-110`, `-32` | electrical/protocol/xHCI error |
| No URBs submitted after open | driver/tty state problem |
| gs_usb has RX URBs submitted but no completions while CAN controller ACKs | xHCI/device USB-IN delivery problem, not CAN |

---

### Phase 3 — Topology tests

Test these combinations with the same smoke script:

```text
A. Pi USB-2 black port, direct
B. Pi USB-3 blue port, direct
C. Powered USB-2 hub into Pi USB-2
D. Powered USB-2 hub into Pi USB-3
E. Different short USB cable
F. Different hub chipset, if available
```

Record for each:

```bash
lsusb -t
dmesg -T | tail -150
# slcan: total raw bytes after O
# gs_usb: candump count and rx_packets
```

Most valuable results:

- **Hub fixes it**: likely VL805/internal hub/full-speed split/TT/topology.
- **No hub fixes it, 6.6 fixes it**: likely kernel regression/quirk.
- **No hub and no kernel fixes it**: consider Pi hardware/EEPROM/adapter firmware edge case.

---

### Phase 4 — Kernel A/B

Run the exact same tests on:

```text
HAOS 15 / 6.6.74:
  slcan raw
  python-can slcan
  gs_usb SocketCAN

HAOS 18 / 6.18.33:
  same tests
```

If HAOS 15 works, preserve:

```bash
uname -a
cat /proc/cmdline
lsusb -t
lsusb -vv -d 16d0:117e
lsusb -vv -d 1d50:606f
dmesg -T | grep -Ei 'usb|xhci|vl805|cdc_acm|gs_usb|can|ttyACM'
```

That gives you the artifact set for a HAOS/Raspberry Pi kernel issue.

---

### Phase 5 — Boot/cmdline experiments feasible on HAOS

Try one at a time:

```text
usbcore.autosuspend=-1
pcie_aspm=off
```

Potential USB quirks for both firmware IDs:

```text
usbcore.quirks=16d0:117e:k,1d50:606f:k
```

Caveat: the `k` quirk is commonly used to disable USB link power management for affected devices, but for a full-speed USB-2 CDC device it may not matter. Treat it as a low-risk experiment, not a primary fix.

I would not expect `dwc_otg.speed=1` to help for the USB-A ports on a Pi 4, because those ports are behind the VL805 xHCI path rather than the older DWC OTG path.

---

## What the `gs_usb` ACK Clue Changes

It changes a lot.

In the `gs_usb` case, the CAN controller/adapter appears to be:

1. Bit-timed correctly.
2. Seeing frames on the bus.
3. ACKing frames at the CAN protocol level.
4. Remaining error-active.
5. Not incrementing Linux `rx_packets`.

That moves the failure boundary downstream of physical CAN reception and upstream of SocketCAN userspace delivery.

The suspect chain becomes:

```text
STM32 FDCAN RX FIFO
  -> adapter firmware RX queue
  -> USB IN endpoint
  -> VL805 xHCI transfer ring / DMA / completion
  -> gs_usb/cdc_acm driver completion callback
  -> netdev/tty userspace
```

Because two different Linux class drivers fail, focus on the shared part:

```text
USB IN endpoint scheduling/completion on Pi 4 VL805 + HAOS kernel
```

The fact that the slcan `V` command returns means the path is not totally dead. The more subtle hypothesis is:

```text
low-duty request/response bulk-IN works,
but continuously queued IN streaming after CAN open does not
```

That is exactly what `usbmon` can expose.

---

## Feasible Fixes / Workarounds Under HAOS Constraints

### Feasible without rebuilding modules

1. **Rollback to HAOS 15 / 6.6.74**
   Best diagnostic and possibly best workaround. If it works, pin that version until HAOS/RPi kernel catches up or a regression is fixed.

2. **Try a powered external USB-2 hub**
   Cheap and high-yield. This changes topology, transaction-translator behavior, and power delivery.

3. **Update Raspberry Pi bootloader / VL805 firmware using a temporary Raspberry Pi OS SD card**
   HAOS may not make this convenient, but booting Raspberry Pi OS solely to run EEPROM updates is practical.

4. **Cmdline mitigations**
   Try `usbcore.autosuspend=-1`, `pcie_aspm=off`, and possibly `usbcore.quirks=16d0:117e:k,1d50:606f:k`.

5. **Use a non-USB CAN interface**
   An MCP2515 or MCP2518FD SPI CAN HAT bypasses VL805 entirely. For a production telemetry appliance on HAOS, this may be the most robust architecture if the USB path remains suspect.

6. **Move CAN ingest off the Pi**
   Use laptop/mini-PC/ESP32/CAN-to-Ethernet/CAN-to-MQTT bridge and let Home Assistant consume telemetry over the network.

### Likely requiring different kernel or hardware

1. **Backporting/changing xHCI/VL805 quirks**
   Properly fixing host-controller behavior requires kernel changes or a kernel update.

2. **Patching `gs_usb` or `cdc_acm`**
   Lower probability and hard on HAOS. Also less compelling because both class paths fail.

3. **Changing CANable firmware USB stack behavior**
   Possible, but not the first move unless `usbmon` shows that the device simply stops producing IN data after open on the Pi.

---

## Recommended Next Steps

1. Run `lsusb -t` and `lsusb -vv` for both firmware modes.
2. Run one `usbmon` capture for slcan failure.
3. Run one `usbmon` capture for `gs_usb` failure.
4. Try a powered external USB-2 hub.
5. Roll back to HAOS 15 / kernel 6.6.74 and repeat the exact smoke tests.
6. If 6.6 works, file a focused issue with:
   - failing/passing kernel versions,
   - Pi 4 model,
   - `lsusb -t`,
   - `lsusb -vv`,
   - dmesg,
   - usbmon pcap,
   - statement that both `cdc_acm` and `gs_usb` fail,
   - statement that laptop works minutes apart on same bus,
   - statement that `gs_usb` ACKs CAN but Linux `rx_packets` remains zero.

---

## Bottom-Line Assessment

The best current hypothesis is:

```text
Pi 4 VL805/xHCI/HAOS-kernel receive-side USB completion problem
```

Most likely practical fixes, in order:

1. **HAOS 15 / 6.6 rollback if it works.**
2. **Powered USB-2 hub if it works.**
3. **Bootloader/VL805 EEPROM update.**
4. **HAOS/kernel update or issue-driven fix.**
5. **SPI CAN HAT to bypass USB entirely.**

The `gs_usb` "ACKed in hardware but not delivered to software" behavior is one of the strongest clues. It places the failure in the adapter-firmware-to-host-software delivery path and makes the kernel-version rollback to 6.6 a well-justified next test.
