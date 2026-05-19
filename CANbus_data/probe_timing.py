import usb.core
import libusb_package
_LIBUSB_BACKEND = libusb_package.get_libusb1_backend()
_orig_find = usb.core.find
def _find_with_libusb_package(*args, **kwargs):
    kwargs.setdefault("backend", _LIBUSB_BACKEND)
    return _orig_find(*args, **kwargs)
usb.core.find = _find_with_libusb_package

from gs_usb.gs_usb import GsUsb
import can

devs = GsUsb.scan()
print(f"found {len(devs)} gs_usb device(s)")
if not devs:
    raise SystemExit("no device")

d = devs[0]
cap = d.device_capability
print(f"fclk_can:    {cap.fclk_can}")
print(f"feature:     {cap.feature}")
print(f"tseg1_min:   {cap.tseg1_min}, tseg1_max: {cap.tseg1_max}")
print(f"tseg2_min:   {cap.tseg2_min}, tseg2_max: {cap.tseg2_max}")
print(f"sjw_max:     {cap.sjw_max}")
print(f"brp_min:     {cap.brp_min}, brp_max: {cap.brp_max}, brp_inc: {cap.brp_inc}")

print("\n--- can.BitTiming.from_sample_point at various rates ---")
for rate in (1_000_000, 500_000, 250_000, 125_000, 100_000, 50_000):
    try:
        bt = can.BitTiming.from_sample_point(
            f_clock=cap.fclk_can, bitrate=rate, sample_point=87.5
        )
        print(f"{rate}: OK  brp={bt.brp} tseg1={bt.tseg1} tseg2={bt.tseg2} sjw={bt.sjw} sp={bt.sample_point:.2f}%")
    except Exception as e:
        print(f"{rate}: FAIL  {type(e).__name__}: {e}")

print("\n--- manual sweep for 250000 ---")
# What BRPs give an integer tq at 250k?
for brp in [1, 2, 4, 5, 8, 10, 16, 20, 25, 32, 40, 50, 64, 80, 100, 125, 160, 200, 250, 320, 500, 1000]:
    tq = cap.fclk_can / (brp * 250_000)
    print(f"  brp={brp:4d}  tq={tq:10.4f}  {'OK' if tq == int(tq) and 4 <= tq <= 25 else ''}")
