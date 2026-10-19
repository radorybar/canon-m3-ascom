"""`canon-m3-ascom detect`: where is the camera and how can CHDK be reached? Used by the installer wizard.

Read-only: lists the camera on USB, finds out which access path works (WinUSB driver or the standard
Windows driver through WPD) and whether CHDK answers. `--json` prints a machine-readable result.
"""

import argparse
import json
import logging
import sys

from .ptp import chdk
from .ptp.transport import CANON_VID, PtpError


def _usb_devices() -> list[str]:
    try:
        import libusb_package
        import usb.core
        devs = usb.core.find(find_all=True, idVendor=CANON_VID, backend=libusb_package.get_libusb1_backend())
        return [f"04A9:{d.idProduct:04X}" for d in devs]
    except Exception as e:  # noqa: BLE001 - detection must not crash
        logging.debug("libusb enumeration failed: %s", e)
        return []


def _probe(open_fn) -> dict:
    """Open a session with open_fn and ask CHDK for its version."""
    try:
        session = open_fn()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    try:
        session.open()
        info = session.device_info()
        major, minor = chdk.ChdkClient(session).get_version()
        return {"ok": True, "model": info.model, "chdk_protocol": f"{major}.{minor}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "camera_seen": True}
    finally:
        session.close()


def detect() -> dict:
    from .ptp.connect import open_winusb, open_wpd
    result = {"usb": _usb_devices()}
    result["winusb"] = _probe(open_winusb)
    try:
        from .ptp.wpd import list_canon_devices
        result["wpd_devices"] = list_canon_devices()
    except Exception as e:  # noqa: BLE001
        result["wpd_devices"] = []
        result["wpd_error"] = str(e)
    result["wpd"] = _probe(open_wpd) if result["wpd_devices"] else {"ok": False, "error": "not listed by WPD"}
    if result["winusb"]["ok"]:
        result["driver"] = "winusb"
    elif result["wpd_devices"]:
        result["driver"] = "standard"
    elif result["usb"]:
        result["driver"] = "other"
    else:
        result["driver"] = "none"
    result["recommended"] = ("winusb" if result["winusb"]["ok"] else "wpd" if result["wpd"]["ok"] else None)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(prog="canon-m3-ascom detect", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    r = detect()
    if args.json:
        print(json.dumps(r))
    else:
        print(f"USB:      {', '.join(r['usb']) or 'no Canon device'}")
        print(f"Driver:   {r['driver']}")
        for name in ("winusb", "wpd"):
            p = r[name]
            state = f"CHDK {p['chdk_protocol']} on {p['model']}" if p["ok"] else p.get("error", "")
            print(f"{name:9} {'OK  ' if p['ok'] else 'no  '} {state}")
        print(f"Use:      {r['recommended'] or 'nothing works yet'}")
    sys.exit(0 if r["recommended"] else 1)
