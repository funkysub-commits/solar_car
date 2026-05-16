import asyncio
from bleak import BleakClient

ADDRESS = input("MAC Address:\n> ")

async def read_chars():
    async with BleakClient(ADDRESS) as client:
        services = client.services

        for service in services:
            for char in service.characteristics:
                if "read" in char.properties:
                    try:
                        data = await client.read_gatt_char(char.uuid)
                        print(f"{char.uuid}: {data.hex()}")
                    except Exception as e:
                        print(f"{char.uuid}: Error - {e}")

asyncio.run(read_chars())