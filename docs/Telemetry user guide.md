### Solar Car Challenge 2026

**Telemetry user guide**

> [!NOTE]
> The ipad will quickly connect to the solar car, but any device can connect to the rpi system with the steps below.

1. power on 12v circuit, it will turn on the rpi and the router.  The eink dashboard should turn on after 30 seconds, that means rpi booted successfully.
> [!IMPORTANT]
> Look at the CAN to USB adapter.
> If it only has the red led on (no green) unplug and replug it until the green led turns on.
2. The clock in upper right of the e-ink should be ticking every few seconds: it is controlled by the rpi and means things look good.  if it stops that means something is wrong, likely power loss.
3. connect phone to the wifi "dd-wrt", there is no password.  it is a LAN wifi (no route to the internet), so you might need to tell your phone this (stay connected anyway, or something along those lines) Phones will want to jump back to mobile data or another wifi where it can see the internet.  The ipad is setup to connect long term.
5. the e-ink dash should display the ip of home assitant.  probbaly `192.168.1.146:8123`
6. put that into your phone browser to see home assistant login.  (make sure you arent doing a google search, just enter as a url)
user:sct, passwd: letsgo

7. when ezkontrol is turned on you should see data from it, see the
"solar car" tab in home assistant on the left in home assistant for most important data.

8. settings/system/network To connect a new cell phone hotspot, this will allow the home assistant to get though to the interent so Euan can help with remote debug. 
details coming here from Evana

> [!IMPORTANT]
> to turn off the home assistant it is best to do a software shutdown before unplugging the power, it avoids possible sdcard corruption.  under solarcar tab, push the shutdown system button at the bottom and wait for screen to show poweroff message, maybe 1 minute.

> [!CAUTION]
> Only the Ezkontrol is connected right now, not the bestgo battery.
> > We never got a chance to test some recent software changes, and will need a little work to try a new CANbus cable. in the meantime, use the Smart BMS app (hold it very close to the battery) to get actual value of SOC, don't trust the battery displays until they are calibrated.


in case of sdcard failure we will have a backup, as well as a backup rpi.

Evana will put her file here
