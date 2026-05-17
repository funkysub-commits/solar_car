import time
import random
import asyncio
import display.bms_gui as bms_gui
from battery_data.smart_bms import SmartBMS, mac


bms = SmartBMS(mac)

async def connect():
    await bms.connect()

async def disconnect():
    await bms.disconnect()

async def get_battery_data():
    soc = await bms.get_battery_percent()
    temp = (await bms.get_temperatures())[0]
    cells = await bms.get_cell_voltages()

    return soc, temp, cells

async def get_cell_count():
    return await bms.get_cell_count()

def telemetry_stream_worker():
    """
    This runs entirely on a background thread.
    Put your Serial, CAN bus, or simulation stream readings here.
    """
    
    print("Background data simulation thread started successfully.")

    try:
        while True:                
            current_soc, current_temp, current_cells = asyncio.run(get_battery_data())
            
            sim_watts = random.uniform(920.0, 1150.0)
            sim_amps = random.uniform(10.2, 14.8)
            sim_mppt = random.uniform(98.1, 99.6)
            
            # 2. Push telemetry numbers into the shared cache array
            bms_gui.battery_soc(current_soc)
            bms_gui.battery_temp(current_temp)
            bms_gui.battery_cells(current_cells)
            bms_gui.solar_performance(sim_watts, sim_amps, sim_mppt)
            
            # Data rate interval pacing (10 Hz)
            time.sleep(0.1)
                
    except Exception as e:
        print(f"Error in data stream: {e}")

def main():
    asyncio.run(connect())

    print("Initializing main thread environment...")
    
    # Start the dashboard and hand it our background processing task
    bms_gui.start_dashboard(num_cells=asyncio.run(get_cell_count()), worker_callback=telemetry_stream_worker)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        asyncio.run(disconnect())
        raise e
    asyncio.run(disconnect())
