import asyncio
import threading
import display.bms_gui as bms_gui
from battery_data.smart_bms import SmartBMS, mac, enable_unsafe_commands


bms = SmartBMS(mac)

_ready = threading.Event()
_cell_count = 16
_shutdown = False
_connect_error: Exception | None = None


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


async def _bms_session():
    """All BLE work runs on this one coroutine / event loop.

    Bleak's notification callbacks hold a reference to the loop the client was
    created on, so connect, reads, and writes must all share a single loop for
    the lifetime of the connection.
    """
    global _cell_count
    try:
        await bms.connect()
        _cell_count = await bms.get_cell_count()
    except Exception as e:
        global _connect_error
        _connect_error = e
        _ready.set()
        return
    _ready.set()

    print("Background data stream started.")
    try:
        while not _shutdown:
            await bms.refresh_all()
            bms_gui.update(bms.info)
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Error in data stream: {e}")
    finally:
        try:
            await bms.disconnect()
        except Exception:
            pass


def _worker_thread():
    try:
        asyncio.run(_bms_session())
    except Exception as e:
        print(f"BMS worker crashed: {e}")
    finally:
        _ready.set()


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

    print("Connecting to BMS...")
    threading.Thread(target=_worker_thread, daemon=True).start()

    if not _ready.wait(timeout=30):
        print("Timed out waiting for BMS connection.")
        return
    if _connect_error is not None:
        print(f"Failed to connect to BMS: {_connect_error}")
        return

    print(f"Connected. Cell count: {_cell_count}")
    print("Initializing main thread environment...")
    try:
        bms_gui.start_dashboard(num_cells=_cell_count)
    finally:
        global _shutdown
        _shutdown = True


if __name__ == '__main__':
    main()
