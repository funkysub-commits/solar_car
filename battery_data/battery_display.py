import time
import random
import bms_gui

def telemetry_stream_worker():
    """
    This runs entirely on a background thread.
    Put your Serial, CAN bus, or simulation stream readings here.
    """
    current_soc = 0.98
    current_temp = 28.5
    
    print("Background data simulation thread started successfully.")
    try:
        while True:
            # 1. Simulate data mutations
            current_soc -= 0.0002
            current_temp += random.uniform(-0.05, 0.08)
            
            live_cells = [random.uniform(3.55, 3.65) for _ in range(16)]
            live_cells[4] = random.uniform(2.85, 2.94)  # Intentionally drop cell 4 low
            
            sim_watts = random.uniform(920.0, 1150.0)
            sim_amps = random.uniform(10.2, 14.8)
            sim_mppt = random.uniform(98.1, 99.6)
            
            # 2. Push telemetry numbers into the shared cache array
            bms_gui.battery_soc(current_soc)
            bms_gui.battery_temp(current_temp)
            bms_gui.battery_cells(live_cells)
            bms_gui.solar_performance(sim_watts, sim_amps, sim_mppt)
            
            # Data rate interval pacing (10 Hz)
            time.sleep(0.1)
            
    except Exception as e:
        print(f"Error in data stream: {e}")

def main():
    print("Initializing main thread environment...")
    
    # Start the dashboard and hand it our background processing task
    bms_gui.start_dashboard(num_cells=16, worker_callback=telemetry_stream_worker)

if __name__ == '__main__':
    main()
