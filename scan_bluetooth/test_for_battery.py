import asyncio
from bleak import BleakClient

ADDRESS = input("MAC Address:\n> ")


def handler(sender, data):
    print(f"{sender}: {data.hex()}")


async def main():
    async with BleakClient(ADDRESS) as client:
        print("Connected:", client.is_connected)

        notify_chars = [
            "0000fff1-0000-1000-8000-00805f9b34fb",
            "02f00000-0000-0000-0000-00000000ff02",
            "02f00000-0000-0000-0000-00000000ff04",
        ]

        for char in notify_chars:
            try:
                await client.start_notify(char, handler)
                print(f"Listening on {char}")
            except Exception as e:
                print(f"Couldn't enable {char}: {e}")

        print("Waiting for data...")
        await asyncio.sleep(20)

asyncio.run(main())