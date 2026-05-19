import time
import asyncio
import display.bms_gui as bms_gui
from battery_data.smart_bms import SmartBMS, mac, enable_unsafe_commands


bms = SmartBMS(mac)


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        ans = input(prompt + suffix).strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


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
    print()
    print("=" * 64)
    print(" Solar Car BMS — startup")
    print("=" * 64)
    print(
        "Restricted commands are BLOCKED by default to prevent accidental\n"
        "BMS damage. They cover:\n"
        "  - Control commands (MOS toggles, balancing, heating, threshold\n"
        "    writes, password change, time sync)\n"
        "  - AT / identity commands (rename, baud rate, firmware query)\n"
        "  - Raw register access (read/write arbitrary registers, history)\n"
        "  - Module-level scanning (scan_for_bms)\n"
        "Reading runtime/settings data is always allowed."
    )
    if _prompt_yes_no(
        "Enable restricted BMS commands for this session?",
        default=False,
    ):
        enable_unsafe_commands(True)
        print(">>> Restricted commands ENABLED for this session.")
    else:
        print(">>> Read-only mode. Restricted calls will raise BMSPermissionError.")
    print()

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
