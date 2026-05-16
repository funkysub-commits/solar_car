import asyncio
from bleak import BleakScanner

DBM_LIMIT = -90
EXCLUDE_APPLE = True # Ignores devices with manufacturer data of {76: ...} (Apple)
EXCLUDE_MICROSOFT = True # Ignores devices with manufacturer data of {6: ...} (Microsoft)

class file_log:
    def __init__(self, f):
        self.file = f
    def fprint(self, string, end="\n"):
        self.file.write(str(string) + str(end))


async def scan_devices():
    print("Scanning for Bluetooth devices...\n")

    with open("result.txt", "w") as f:
        logs = file_log(f)

        try:
            devices = await BleakScanner.discover(return_adv=True)

            if not devices:
                logs.fprint("No Bluetooth devices found.")
                return

            logs.fprint(f"Found {len(devices)} devices:\n")

            for address, (device, advertisement) in devices.items():
                if ((not EXCLUDE_APPLE) or (not 76 in advertisement.manufacturer_data.keys())) and ((not EXCLUDE_MICROSOFT) or (not 6 in advertisement.manufacturer_data.keys())) and advertisement.rssi > DBM_LIMIT:
                    logs.fprint("=" * 60)
                    logs.fprint(f"Name:              {device.name or 'Unknown'}")
                    logs.fprint(f"Address:           {device.address}")
                    logs.fprint(f"RSSI:              {advertisement.rssi} dBm")

                    if hasattr(device, "details"):
                        logs.fprint(f"System Details:    {device.details}")

                    logs.fprint(f"TX Power:          {advertisement.tx_power}")
                    logs.fprint(f"Local Name:        {advertisement.local_name}")
                    logs.fprint(f"Manufacturer Data: {advertisement.manufacturer_data}")
                    logs.fprint(f"Service Data:      {advertisement.service_data}")
                    logs.fprint(f"Service UUIDs:     {advertisement.service_uuids}")
                    logs.fprint(f"Platform Data:     {advertisement.platform_data}")

            logs.fprint("=" * 60)

        except Exception as e:
            logs.fprint(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(scan_devices())