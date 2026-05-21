# <img src="./readme_assets/solar_storms_logo.png" width="4%"> Solar Storms Telemetry System
**Raspberry Pi + Home Assistant Setup Guide**  
---



**Hardware**: Raspberry Pi 4, Waveshare 7.5" e-ink display, DSD TECH SH-C31G USB-CAN adapter  
**Network**: ASUS router and cabling, optional cell phone with hotspot enabled  
**Software**: Home Assistant OS, Docker containers, Python + gpiod + SocketCAN  
**Interfaces**: SPI (display), CAN bus (motor controller), Bluetooth (battery BMS), Ethernet and Wifi  
**Features**: Live dashboard, configurable e-ink display, CAN bus decoder, bluetooth battery connection  

Devices on solar car:
- Battery: bestgo
- BWPFE51100ATIPF (Os as zeros) — 51V 100Ah PFE-series LiFePO4 pack
- Motor control : ezkontrol



## Table of contents

1. [System overview](#1-system-overview)
2. [Prerequisites](#2-prerequisites)
3. [Initial setup: Home Assistant OS](#3-initial-setup-home-assistant-os)
4. [Waveshare 7.5" e-ink display setup](#4-waveshare-75-e-ink-display-setup)
5. [E-ink display: Home Assistant integration](#5-e-ink-display-home-assistant-integration)
6. [CAN bus: EZkontrol motor controller](#6-can-bus-ezkontrol-motor-controller)
7. [Battery BMS: Bluetooth integration](#7-battery-bms-bluetooth-integration)
8. [System maintenance & commands](#8-system-maintenance--commands)
9. [Network setup discussion](#9-network-setup-discussion)
10. [Andy left to do](#10-andy-left-to-do)
11. [Student to do (with Andy help)](#11-student-to-do-with-andy-help)

## 1. System overview
This guide documents the complete setup of a solar car monitoring system built on a Raspberry Pi 4 running Home Assistant OS. The system integrates three external hardware interfaces and presents data through both a web dashboard and a physical e-ink display.

![System Diagram](readme_assets/diagram1.png)



### Architecture
The system consists of three external devices connected to a Raspberry Pi 4:  
**EZkontrol B48800** — 48V BLDC motor controller connected via CAN bus through a DSD TECH SH-C31G USB-to-CAN adapter. Broadcasts voltage, current, speed, temperatures, and error status every 100ms.  
**Battery pack (B00016 BMS)** — Connected via Bluetooth using the BLE Battery Management System integration in Home Assistant. Provides stored energy, battery level, and other BMS data.  
**Waveshare 7.5" V2 e-ink display** — Connected via the SPI bus and GPIO pins through the e-Paper Driver HAT. Displays configurable sensor data from Home Assistant, refreshing every 5 minutes with an on-demand refresh button.  
### Software architecture
Two services run on the Pi alongside Home Assistant OS:
| Service | Purpose | Key detail |
| --- | --- | --- |
| solar-car-canbus | HA app — reads the CAN bus, pushes sensors to HA | both devices on one shared bus, REST API |
| epaper-display | Reads HA sensors, draws to e-ink screen | Configurable via HA helpers |


The `solar-car-canbus` app is managed by Home Assistant — it brings up the CAN interface itself and restarts with HA. The e-ink display runs with access to /dev for SPI/GPIO.  

## 2. Prerequisites
### Hardware
| Component | Details |
| --- | --- |
| Raspberry Pi 4 | 4GB+ RAM recommended |
| microSD card | 32GB+ for Home Assistant OS |
| Waveshare 7.5" V2 e-Paper HAT | 800x480 resolution, SPI interface |
| DSD TECH SH-C31G | USB-B to CAN adapter, candlelight firmware |
| EZkontrol B48800 | 48V BLDC motor controller with CAN output |
| Battery with BLE BMS | Bluetooth-enabled battery management system |
| USB power supply | 5V 3A+ for the Raspberry Pi |


### Software
Built with Claude in this [context](https://claude.ai/share/ac0488df-1b63-45a8-bcfc-a0aab504916a)  

Home Assistant OS (HAOS) installed on the Pi via Raspberry Pi Imager. The Advanced SSH & Web Terminal add-on must be installed with Protection Mode disabled to allow Docker access and GPIO/SPI operations.  
A Long-Lived Access Token from Home Assistant is required for the containers to communicate with the HA REST API. Generate this from your HA profile page.  

## 3. Initial setup: Home Assistant OS
### 3.1 Flash HAOS to SD card
Use Raspberry Pi Imager to flash Home Assistant OS onto a microSD card. Select your Pi 4 as the device, choose 'Other specific-purpose OS > Home assistants and home automation > Home Assistant > Home Assistant OS'. Configure WiFi and SSH in the imager settings if desired.  
Insert the SD card into the Pi and power on. Wait several minutes for the first boot. Access the web interface at [http://homeassistant.local:8123](http://homeassistant.local:8123).  

### 3.2 Install SSH add-on
From the HA web interface:  
1. Go to Settings > Add-ons > Add-on Store
2. Search for Advanced SSH & Web Terminal
3. Install it and configure a password or SSH key
4. Disable Protection Mode (toggle in add-on settings) — required for Docker and hardware access
5. Start the add-on
> [!WARNING]
> Disabling Protection Mode gives the SSH add-on full system access including Docker. Only do this if you trust your network environment.
### 3.3 Install BLE Battery Management System
1. Go to Settings > Devices & Services > Integrations
2. Click + Add Integration
3. Search for BLE Battery Management System
4. Follow the on-screen instructions to discover and pair your battery's Bluetooth BMS
5. Once connected, battery sensors will appear automatically
> [!NOTE]
> The BLE BMS integration discovers batteries automatically over Bluetooth. Make sure the battery is powered on and within range.
### 3.4 Generate long-lived access token
1. Click your profile icon (bottom left of HA sidebar)
2. Scroll to Long-Lived Access Tokens
3. Click Create Token, give it a name (e.g., 'Docker Containers')
4. Copy the full token immediately — it won't be shown again
```text
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIyNDlhZWQ1NTFlZDk0MWVjOGM4NGI3MDU1MTk1Mzk3ZSIsImlhdCI6MTc3Mzg5OTEwMiwiZXhwIjoyMDg5MjU5MTAyfQ.CveoN77vg-21Eq3oJ1e_7FWMyCRhfKq0H0AS50mO7JE
```
5. Save it securely — you'll need it for both Docker containers

> [!NOTE]
> Home Assistant gives a warning in the logs about other software running, it can be ignored.

## 4. Waveshare 7.5" e-ink display setup
### 4.1 Physical connection
> [!CAUTION]
> Power off the Raspberry Pi before connecting any hardware.
1. Plug the e-Paper Driver HAT onto the Pi's 40-pin GPIO header, aligning the pins carefully
2. Connect the ribbon cable from the display to the HAT connector — lift the small black latch, slide the ribbon in, press the latch back down
3. Power the Pi back on


### 4.2 Verify SPI and environment
SSH into the Pi and verify:
```zsh
ls /dev/spi*
# Should show: /dev/spidev0.0  /dev/spidev0.1

python3 --version
# Should show Python 3.12+

pip3 install RPi.GPIO spidev Pillow numpy gpiozero gpiod
```
### 4.3 Key challenge: HAOS container restrictions
Home Assistant OS runs add-ons in sandboxed Docker containers. Even with Protection Mode disabled, the SSH add-on cannot directly open SPI devices. The solution is to run the display code in a separate privileged Docker container with /dev mounted.  
Additionally, the Waveshare Python library uses gpiozero which doesn't work in this environment. The library must be patched to use gpiod (the modern Linux GPIO character device interface) instead. The CS pin (GPIO 8) must also be excluded since SPI hardware manages it automatically.  
### 4.4 Building the patched Docker image
Create the project directory:
```zsh
mkdir -p /config/epaper-display
cd /config/epaper-display
```
##### patch.py — patches Waveshare library to use gpiod
The patch script replaces all gpiozero references in epdconfig.py with gpiod equivalents. Key changes:
- Replaces gpiozero.LED and gpiozero.Button with gpiod.request_lines
- Excludes GPIO 8 (CS pin) since SPI manages it
- Replaces digital_write/digital_read with gpiod set_value/get_value
- Updates module_init and module_exit to use gpiod
> [!NOTE]
> The full patch.py is stored in /config/epaper-display/ on your Pi.
##### Dockerfile
```dockerfile
FROM python:3.12-alpine

RUN apk add --no-cache git gcc musl-dev linux-headers ttf-dejavu
RUN pip install --no-cache-dir spidev gpiod Pillow numpy requests
RUN git clone https://github.com/waveshare/e-Paper.git /e-Paper

COPY patch.py /patch.py
RUN python3 /patch.py

COPY display.py /display.py
CMD ["python3", "/display.py"]
```
##### Build and test
docker build -t epaper-display /config/epaper-display  

# Quick test - run the Waveshare demo
```zsh
docker run --rm -it --privileged -v /dev:/dev epaper-display sh
cd /e-Paper/RaspberryPi_JetsonNano/python/examples
python3 epd_7in5_V2_test.py
```

## 5. E-ink display: Home Assistant integration
### 5.1 Display script
The display script (display.py) reads configuration from Home Assistant helpers to determine what data to show. It supports a configurable title and 4 sensor slots. Every 5 minutes (or on button press), it fetches the slot entity IDs from HA helpers, resolves each to its current sensor state, and renders everything to the e-ink display.  
Key features: reads input_text helpers for title and slot entity IDs, fetches each sensor's friendly_name/state/unit, renders a clean layout, and polls for the refresh button every 5 seconds during sleep.  
### 5.2 Create HA helpers
In Home Assistant, go to Settings > Devices & Services > Helpers and create:  
| Helper type | Name | Default value |
| --- | --- | --- |
| Text | EInk Display Title | Solar Car Monitor |
| Text | EInk Slot 1 | sensor.ezkontrol_motor_speed |
| Text | EInk Slot 2 | sensor.ezkontrol_controller_temp |
| Text | EInk Slot 3 | sensor.p_24050bnna70_b00016_stored_energy |
| Text | EInk Slot 4 | sensor.p_24050bnna70_b00016_battery |
| Button | EInk Refresh | (no default needed) |


Set the values via Developer Tools > Services > input_text.set_value after creation.  
### 5.3 Dashboard control card
Add an Entities card to your dashboard with this YAML:  
```yaml
type: entities
title: E-Ink Display Control
entities:
  - entity: input_text.eink_display_title
    name: Display Title
  - entity: input_text.eink_slot_1
    name: Slot 1
  - entity: input_text.eink_slot_2
    name: Slot 2
  - entity: input_text.eink_slot_3
    name: Slot 3
  - entity: input_text.eink_slot_4
    name: Slot 4
  - entity: input_button.eink_refresh
    name: Refresh Display Now
```
### 5.4 Run the display container
```zsh
docker run -d --name epaper-display \
  --privileged \
  --restart=unless-stopped \
  --network=host \
  -v /dev:/dev \
  -e HA_URL="http://100.100.79.71:8123" \
  -e HA_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIyNDlhZWQ1NTFlZDk0MWVjOGM4NGI3MDU1MTk1Mzk3ZSIsImlhdCI6MTc3Mzg5OTEwMiwiZXhwIjoyMDg5MjU5MTAyfQ.CveoN77vg-21Eq3oJ1e_7FWMyCRhfKq0H0AS50mO7JE" \
  -e INTERVAL=300 \
  epaper-display
```

## 6. CAN bus: EZkontrol motor controller
### 6.1 Hardware wiring
Connect the EZkontrol's CN2 connector to the DSD TECH SH-C31G USB-CAN adapter:
| EZkontrol pin | Wire color | Signal | SH-C31G terminal |
| --- | --- | --- | --- |
| CN2-10 | Yellow | CAN_H | CAN_H |
| CN2-21 | Green | CAN_L | CAN_L |
| CN2-22 | Black | CAN_GND | GND |


> [!NOTE]
> Enable the 120 ohm termination resistor on the SH-C31G if it is the last device on the CAN bus. The EZkontrol has its own 120 ohm termination enabled by default (brown wire CN2-11).
### 6.2 CAN protocol: MCU-to-METER (read-only)
The EZkontrol broadcasts two J1939 extended CAN frames every 100ms. This is a passive protocol — no handshake required.  
The solar car runs the EZkontrol motor controller and the BESTGO battery on **one shared CAN bus at 500 Kbps**, so the EZkontrol must use 500 Kbps too. Use the “EZ-Tune” Android app to set its CAN protocol to **102** (the 500 Kbps variant; protocol 2 is the 250 Kbps variant, and the controller shipped defaulted to 1). The setting persists across power cycles.  
##### Message I — CAN ID 0x180117EF
| Bytes | Data | Resolution | Offset | Range |
| --- | --- | --- | --- | --- |
| 0-1 | Bus voltage | 0.1 V/bit | 0 | 0 - 300 V |
| 2-3 | Bus current | 0.1 A/bit | -3200 A | -3200 - 3200 A |
| 4-5 | Phase current | 0.1 A/bit | -3200 A | -3200 - 3200 A |
| 6-7 | Speed | 0.1 rpm/bit | -32000 rpm | -32000 - 32000 rpm |


All values are little-endian unsigned 16-bit. Formula: physical = raw * resolution + offset  
#### Message II — CAN ID 0x180217EF
| Byte | Data | Resolution | Offset |
| --- | --- | --- | --- |
| 0 | Controller temp | 1 C/bit | -40 C |
| 1 | Motor temp | 1 C/bit | -40 C |
| 2 | Throttle % | 1%/bit | 0 |
| 3 | Status (gear, brake, mode) | Bitfield | — |
| 4-6 | Error flags (22 faults) | Bitfield | — |
| 7 | Life signal (bits 7-4) | Counter | 0 |


### 6.3 The solar-car-canbus app
One Home Assistant **app** (HA's current term for what used to be called an
"add-on"), `solar-car-canbus`, decodes the CAN bus. It reads **both** the
EZkontrol motor controller and the BESTGO battery from the single shared
bus, decodes them, and pushes named sensors to Home Assistant over the REST
API. The two devices coexist on one bus because their IDs don't overlap —
the EZkontrol uses 29-bit extended IDs (`0x1801xxxx`), the BESTGO BMS uses
11-bit standard IDs (`0x351`–`0x379`).

The app source is in `ha_addons/solar-car-canbus/` in this repo; on the Pi
it is placed in `/addons/solar-car-canbus/` and installed from **Settings >
Apps > App Store > Local apps**.

It publishes 34 sensors:
- 13 `sensor.ezkontrol_*` — bus voltage/current, phase current, motor speed,
  controller/motor temperature, throttle, gear, brake, contactor, errors.
- 21 `sensor.bestgo_*` — SOC/SOH, pack voltage/current/temperature, cell
  min/max voltage and temperature, charge/discharge limits, alarms, capacity.

`run.sh` brings up the `can0` interface at the configured bitrate before
starting. If the USB-CAN adapter came up in STM32 DFU mode (so there is no
`can0`), it first attempts a `uhubctl` USB port power-cycle to recover it.

### 6.4 App configuration
Set these in the app's **Configuration** tab:
| Option | Default | Purpose |
| --- | --- | --- |
| can_interface | can0 | SocketCAN interface name |
| can_bitrate | 500000 | shared-bus bitrate for both devices |
| ezkontrol_dummy | false | simulate the motor controller instead of decoding it |
| ezkontrol_push_interval | 2 | seconds between EZkontrol sensor pushes |
| bestgo_dummy | false | simulate the battery instead of decoding it |
| bestgo_push_interval | 5 | seconds between BESTGO sensor pushes |

Each device has its own dummy flag and push interval, so one can run live
while the other is simulated. With both `*_dummy` set to `true` the app
skips the CAN interface entirely — handy for testing with no hardware.

## 7. Battery BMS: Bluetooth integration
The battery pack connects to Home Assistant over Bluetooth using the BLE Battery Management System integration. This was set up during initial configuration (Section 3.3).  
Key sensors created by this integration include:  
`sensor.p_24050bnna70_b00016_stored_energy` — total energy stored in the battery  
`sensor.p_24050bnna70_b00016_battery` — battery percentage / state of charge  

> [!NOTE]
> You can rename sensor friendly names for shorter display labels: go to Settings > Devices & Services > Entities, click the sensor, gear icon, and change the Name field.

## 8. System maintenance & commands
### Container management
| Task | Command |
| --- | --- |
| View running containers | `docker ps` |
| View e-ink logs | `docker logs -f epaper-display` |
| View solar-car-canbus logs | `ha apps logs local_solarcar_canbus` |
| Restart e-ink display | `docker restart epaper-display` |
| Restart solar-car-canbus | `ha apps restart local_solarcar_canbus` |
| Stop e-ink before shutdown | `docker stop epaper-display` |
| Remove a container | `docker rm -f <container_name>` |
| Rebuild e-ink image | `docker build -t epaper-display /config/epaper-display` |
| Rebuild solar-car-canbus | `ha apps rebuild local_solarcar_canbus` |


### CAN bus diagnostics
```zsh
# Verify USB-CAN adapter is detected
dmesg | grep -i can
lsusb | grep canable

# Bring up CAN interface manually
ip link set can0 type can bitrate 250000
ip link set can0 up

# Sniff raw CAN traffic (inside privileged container)
candump can0
```
### E-ink display diagnostics
```zsh
# Clear the display manually
docker stop epaper-display && docker rm epaper-display
docker run --rm -it --privileged -v /dev:/dev epaper-display sh
# Then inside the shell:
python3 << 'EOF'
import sys
sys.path.append('/e-Paper/RaspberryPi_JetsonNano/python/lib')
from waveshare_epd import epd7in5_V2
epd = epd7in5_V2.EPD()
epd.init()
epd.Clear()
epd.sleep()
print("Screen cleared")
EOF
```
### Fan control
The official Raspberry Pi fan is controlled at the firmware level. Access the host and edit config.txt:
```zsh
# Access the host shell
docker run --rm -it --privileged --pid=host \
  alpine nsenter -t 1 -m -u -n -i sh

# Add fan control (turns on at 60C)
echo "dtoverlay=gpio-fan,gpiopin=14,temp=60000" \
  >> /mnt/boot/config.txt

# Exit and reboot
exit
ha host reboot
```



All project files are stored in /config/epaper-display/ and /config/can-reader/ on the Raspberry Pi. These directories persist across reboots.  
## 9. Network setup discussion

![Diagram](readme_assets/diagram2.png)

The solar car needs a connection to the chase vehicle to provide a way to view telemetry data.  

Raspberry pi is connected to an old Asus router.  The Asus router helped when setting up and debugging the system.  The raspberry pi has a wifi interface and can provide a wifi access point, but range is not tested. The asus router will provide a strong wifi connection to the home assistant on the road, but does take a small amount of extra power from the battery.  We may want to remove the Asus router and just have a laptop direct connect to the raspberry pi wifi access point.  However, with the router, we should have longer range, and also the ability for the raspberry pi to connect to a cell phone hot spot at the same time for remote monitoring/debug over the internet when a cell phone data connection is available.  

It might be annoying for the PC in the chase vehicle to have telemetry data but not have an internet connection for debug!  

Instructions to setup a different cell phone hotspot and password: Through the home assistant GUI (to be described later)  

## 10. Andy left to do
- [https://goldenmotor.bike/products/ezkontrol-48-volt-universal-bldc-controller](https://goldenmotor.bike/products/ezkontrol-48-volt-universal-bldc-controller)
- [https://www.ytk-group.co.jp/products/wp-content/uploads/2023/02/EZkontrol-CANBUS-MCU-to-VCU-V1.0-20221001.pdf](https://www.ytk-group.co.jp/products/wp-content/uploads/2023/02/EZkontrol-CANBUS-MCU-to-VCU-V1.0-20221001.pdf)
- [https://goldenmotor.bike/products/ezkontrol-48-volt-universal-bldc-controller?variant=45701095358709](https://goldenmotor.bike/products/ezkontrol-48-volt-universal-bldc-controller?variant=45701095358709)


- **Security (deferred):** this README has a Home Assistant long-lived token in plaintext (sections 3.4 and 5.4) and HA login creds (`sct/letsgo`, in the Tailscale notes below) — committed and pushed to GitHub. Rotate the token, change the password, and replace both with placeholders + env vars. They are already in git history, so treat them as compromised.
- Debug can bus!!  Check wire connections!
    - Wire up CAN bus properly and switch to live mode
    - Debug CAN connection (termination switch, candump test)
    - Eztune app:
        - Verify EZkontrol baud rate setting via Bluetooth app (should be 250K, protocol = ??)
- 2026-05-20
    - PC to ezkontrol working at 250k
    - PC to bestgo working at 500k
    - Switch ezkontrol to 500k and test
    - Integrate both into an can control reader on the pc
        - Make it work regardless of what connected to
    - Move that to basic home assistant docker container, test dashboard
    - Create addon for home assistant “CAN bus reader”
    - Test in the lab!!
    - Update e-ink display to make prettier  
After a reboot:  
```zsh
docker stop can-reader && docker rm can-reader
docker ps | grep can
docker run --rm -it --privileged --network=host -v /dev:/dev can-reader sh

ip link set can0 down
ip link set can0 type can bitrate 250000
ip link set can0 up
candump can0
```

If still nothing after the power cycle and swap, let's try the handshake. The VCU protocol requires the controller to receive a 0xAA response before it starts broadcasting. Try sending it:  
```zsh
cansend can0 18EF00D0#AA00000000000000
```
Then immediately:

```zsh
candump can0
Try these in order and let me know what happens at each step.
cansend can0 18EF00D0#AA00000000000000
candump can0
```



Complete:
- Enable Tailscale to connect to home assistant via the school wifi
    - Home assistant added school wifi, should connect automatically
    - Installed tailscale app on windows, addon on home assistant, find IP and connect
        - Connected to funkysub@gmail.com account for now 
        - Recent: [http://100.100.79.71:8123/](http://100.100.79.71:8123/)  sct/letsgo
        - [https://login.tailscale.com/admin/welcome](https://login.tailscale.com/admin/welcome)
        - UNFORTUNATELY tailscale blocked by school wifi
	
> [!IMPORTANT]
> Changed network name to:SolarStormsHomeAssistant (document this)

## 11. Student to do (with Andy help)

- Decide if you want to use wifi router for communication to the chase vehicle, or another idea?
- In car power for the raspberry pi, wifi router
- 3d model and print an enclosure for the display, raspberry pi, usb to can dongle.
- Mount router inside vehicle?
- Backup sdcard that can be swapped out if original fails
- Home assistant alerts for: low storage space, critical home assistant errors, docker failures, etc, etc
- Create screens for telemetry on chase vehicle with home assistant software
    - Look at telemetry video and code to see what that team thinks is important
- Decide what to display on e-ink dashboard display and implement
    - Currently simple display of 4 random sensors, clunky setup, slow refresh
    - This V2 device can do fast updates on small portion of the screen (spedometer for example)
    - Minimize screen flashes on update?
    - Esp32 board alternative?
###### Ideas for improvement:
the Waveshare 7.5" V2 supports both fast refresh and partial refresh in the Python library. We just need to update the display script. Here's what we can improve:  
**Fast refresh** — uses `init_fast()` instead of `init()`, cuts the full refresh from ~6 seconds to ~2 seconds with less flashing.  
**Partial refresh** — only redraws the pixels that changed. No flash at all. Perfect for updating just the sensor values while keeping the layout static.  
**Better approach**: Do a full refresh once on startup to set a clean base image, then use partial refresh for subsequent updates. Do a full refresh every ~30 cycles to prevent ghosting.
Can get pretty fancy with this, but simple might be a good idea.  




![Evana](readme_assets/evana.png)