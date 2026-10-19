"""First-contact diagnostics for the camera over CHDK PTP.

  python -m canon_m3_ascom.ptpdiag              # read-only checks: USB, PTP, CHDK, camera info
  python -m canon_m3_ascom.ptpdiag --shoot 5    # also take one 5 s raw frame over USB and analyse it
  python -m canon_m3_ascom.ptpdiag --fake ...   # same against the emulated camera

Each step is reported separately so a failure points at the layer that broke.
"""

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import rawcodec
from .backends.base import BackendError
from .backends.ptp import PtpBackend
from .config import Config
from .ptp import chdk, scripts
from .ptp.connect import TRANSPORTS, open_session
from .ptp.transport import CANON_VID, PtpError, PtpSession
from .sensor import EOS_M3


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def step(title: str) -> None:
    print(f"\n{title}")


def list_usb() -> None:
    import libusb_package
    import usb.core
    devs = list(usb.core.find(find_all=True, idVendor=CANON_VID, backend=libusb_package.get_libusb1_backend()))
    if devs:
        ok(", ".join(f"Canon device 04A9:{d.idProduct:04X}" for d in devs))
    else:
        fail("no Canon device on USB - connect the camera and switch it on (with CHDK loaded)")


def run(args) -> int:
    if args.fake:
        from .ptp.fake import FakeCamera, FakeTransport
        camera = FakeCamera(time_scale=0.0)
        make_transport = lambda: FakeTransport(camera)  # noqa: E731
    else:
        step("1. USB")
        list_usb()
        make_transport = lambda: open_session(args.transport, args.pid)  # noqa: E731

    step(f"2. Open the camera (transport: {args.transport})")
    try:
        made = make_transport()
    except PtpError as e:
        fail(str(e))
        return 1
    session = made if hasattr(made, "transaction") else PtpSession(made)
    is_wpd = type(session).__name__ == "WpdSession"
    ok("standard Windows driver via WPD (no driver change)" if is_wpd
       else f"WinUSB, max packet {session.t.max_packet} B")

    try:
        step("3. PTP session")
        session.open()
        info = session.device_info()
        ok(f"{info.manufacturer} {info.model}, firmware {info.version}, serial {info.serial}")
        if is_wpd:
            ops = info.operations
            ok(f"camera advertises {len(ops)} vendor operations; CHDK 0x{chdk.OC_CHDK:04X} "
               f"{'is listed' if chdk.OC_CHDK in ops else 'is not listed (normal for CHDK, sent anyway)'}")

        step("4. CHDK")
        client = chdk.ChdkClient(session)
        major, minor = client.get_version()
        ok(f"CHDK PTP protocol {major}.{minor}")

        step("5. Lua on the camera (read-only)")
        build, platform, temp, support, mode = scripts.parse_result(client.execwait(scripts.INFO))
        ok(f"CHDK {build} for {platform}, sensor {temp} C, camera in {mode} mode")
        support = int(support)
        if support & chdk.CAPTURE_RAW:
            ok(f"USB remote capture supported (formats 0x{support:X}: raw{', dng' if support & 4 else ''})")
        else:
            fail(f"USB remote capture of raw is NOT supported by this build (formats 0x{support:X})")
            return 1
    except PtpError as e:
        fail(str(e))
        return 1
    except ValueError as e:
        fail(f"camera script: {e}")
        return 1
    finally:
        session.close()

    if not args.shoot:
        print("\nAll read-only checks passed. Next: --shoot 5")
        return 0

    step(f"6. Raw capture over USB: {args.shoot} s, ISO {args.iso}")
    print("     (camera: Manual mode, 'Release shutter w/o lens' enabled when on the telescope)")
    out = Path(args.out)
    cfg = Config()
    cfg.ptp.fake = args.fake
    cfg.ptp.transport = args.transport
    cfg.ptp.keep_raw = True  # keep the untouched buffer, incl. the masked border
    cfg.camera.frames_dir = str(out)
    backend = PtpBackend(cfg, transport_factory=make_transport)
    try:
        backend.connect()
        ok(f"connected, record mode, sensor {backend.sensor_temperature()} C")
        t0 = time.monotonic()
        img = backend.capture(args.shoot, args.iso, True, threading.Event())
        total = time.monotonic() - t0
    except BackendError as e:
        fail(str(e))
        return 1
    finally:
        backend.disconnect()
    ok(f"frame {img.shape[1]}x{img.shape[0]} in {total:.1f} s total ({total - args.shoot:.1f} s overhead)")

    raw_path = max(out.glob("m3_*.raw"), key=lambda f: f.stat().st_mtime)
    full = rawcodec.unpack14(raw_path.read_bytes(), EOS_M3.raw_width, EOS_M3.raw_height, word_swapped=True)
    border = full[EOS_M3.active_top:EOS_M3.active_bottom, 8:EOS_M3.active_left - 8]
    ok(f"masked border (optical black): median {np.median(border):.0f}, std {border.std():.1f} "
       f"-> black level; CHDK port says {EOS_M3.black_level}")
    p = np.percentile(img, [0.1, 50, 99.9])
    ok(f"active area: min {img.min()}, median {p[1]:.0f}, 99.9% {p[2]:.0f}, max {img.max()} "
       f"(expect median near black level {EOS_M3.black_level} for a dark/short frame)")
    planes = {"R": img[0::2, 0::2], "G1": img[0::2, 1::2], "G2": img[1::2, 0::2], "B": img[1::2, 1::2]}
    ok("channel medians " + ", ".join(f"{k} {np.median(v):.0f}" for k, v in planes.items())
       + " (G1 and G2 should match; R/B differ under colored light if the CFA is RGGB)")
    ok(f"saved {raw_path}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m canon_m3_ascom.ptpdiag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shoot", type=float, default=0, help="take one raw frame of this many seconds")
    ap.add_argument("--iso", type=int, default=800)
    ap.add_argument("--out", default="frames", help="where to save the test frame")
    ap.add_argument("--pid", type=lambda s: int(s, 16), default=0, help="USB product id (hex) if several cameras")
    ap.add_argument("--fake", action="store_true", help="use the emulated camera")
    ap.add_argument("--transport", choices=TRANSPORTS, default="auto",
                    help="winusb (Zadig/libwdi driver), wpd (standard Windows driver) or auto")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="     %(message)s")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
