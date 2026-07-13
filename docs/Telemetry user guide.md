### Solar Car Challenge 2026

**Telemetry user guide**

> [!NOTE]
> The ipad will quickly connect to the solar car, but any device can connect to the rpi system with the steps below.



1. power on 12v circuit, it will turn on the rpi and the router.  eink dashboard should turn on after 30 seconds, that means rpi booted successfully.
> [!IMPORTANT]
> Look at the CAN to USB adapter.
> If it only has the red led on (no green) unplug and replug it until the green led turns on.
2. The clock in upper right should be ticking every few seconds: it is controlled by the rpi and means things look good.  if it stops that means something is wrong, likely power loss.
3. connect phone to the wifi "dd-wrt", there is no password.  it is a LAN wifi (no route to the internet), so you might need to tell your phone this (stay connected anyway, or something along those lines) 
4. Phones will want to jump back to mobile data or another wifi where it can see the internet.  The ipad is already set to stay connected.
5. the e-ink dash should display the ip of home assitant.  probbaly `192.168.1.146:8123`
6. put that into your phone browser to see home assistant login.  (make sure you arent doing a google search, just entering as a url)
user:sct, passwd: letsgo

7. when ezkontrol is turned on you should see data from it, see the
"solar car" tab in home assistant on the left in home assistant.

8. connect new cell phone hotspot, this will allow Euan to help with remote devug.
to be filled in by Evana (setting/network)

> [!IMPORTANT]
> to turn off the home assistant it is best to power it off, avoids any sdcard corruption.  under solarcar tab, push the shutdown system button at the bottom and wait for screen to show poweroff message, maybe 1 minute.

> [!CAUTION]
> Only the Ezkontrol is connected right now, not the bestgo.
> > though it's data can be accessed via the Smart BMS app

Evana will put her file here
