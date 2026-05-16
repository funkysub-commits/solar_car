import asyncio
from bleak import BleakClient

ADDRESS = input("MAC Address:\n> ")


async def inspect():
    async with BleakClient(ADDRESS) as client:
        print("Connected:", client.is_connected)

        services = client.services

        for service in services:
            print(f"\nService: {service.uuid}")

            for char in service.characteristics:
                print(f"  Characteristic: {char.uuid}")
                print(f"  Properties: {char.properties}")


asyncio.run(inspect())