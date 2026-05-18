import time
import asyncio
import display.bms_gui as bms_gui
from battery_data.smart_bms import SmartBMS, mac


bms = SmartBMS(mac)


def telemetry_stream_worker():
    """
    Background thread that polls the BMS and pushes snapshots to the GUI.
    """
    print("Background data simulation thread started successfully.")

    try:
        while True:
            asyncio.run(bms.refresh_all())
            bms_gui.update(bms.info)
            time.sleep(0.1)
    except Exception as e:
        print(f"Error in data stream: {e}")



def main():
    asyncio.run(bms.connect())

    print("Initializing main thread environment...")

    bms_gui.start_dashboard(
        num_cells=asyncio.run(bms.get_cell_count()),
        worker_callback=telemetry_stream_worker,
    )

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        asyncio.run(bms.disconnect())
        raise e
    asyncio.run(bms.disconnect())
