"""Stand-in for chdkptp.exe that answers the batch commands the CHDK backend sends.

Run as `python -m canon_m3_ascom.fakechdk -c -e"rec" -e"rs <dest> -raw -tv=5 -svm=800"`.
Lets the real ChdkBackend (subprocess, file handling, raw decoding) be tested without a camera.

Environment:
  FAKE_CHDK_TIME_SCALE   multiply exposure sleeps (default 1.0; tests use 0)
  FAKE_CHDK_FOCUSER_URL  Alpaca focuser to couple star sharpness to
  FAKE_CHDK_FAIL         "connect" -> behave like no camera attached
  FAKE_CHDK_TEMP         sensor temperature to report (default 21)
"""

import os
import shlex
import sys
import time
from fractions import Fraction
from pathlib import Path

from . import focuser, rawcodec, starfield
from .sensor import EOS_M3


def _parse_tv(s: str) -> float:
    return float(Fraction(s)) if "/" in s else float(s)


def _remoteshoot(args: list[str]) -> None:
    dest = None
    tv, iso, fmt = 1.0, 100, "jpg"
    for a in args:
        if a.startswith("-tv="):
            tv = _parse_tv(a[4:])
        elif a.startswith("-svm="):
            iso = int(a[5:])
        elif a in ("-raw", "-dng", "-jpg"):
            fmt = a[1:]
        elif not a.startswith("-"):
            dest = a
    if dest is None or fmt != "raw":
        print(f"ERROR: fake chdkptp supports only 'rs <dest> -raw', got {args}")
        sys.exit(1)
    time.sleep(tv * float(os.environ.get("FAKE_CHDK_TIME_SCALE", "1")))
    focus = focuser.read_position(os.environ.get("FAKE_CHDK_FOCUSER_URL", ""))
    img = starfield.render(EOS_M3, tv, iso, True, focus)
    Path(dest + ".raw").write_bytes(rawcodec.encode_chdk_raw(img, EOS_M3))
    print(f"{dest}.raw")


def main(argv: list[str]) -> int:
    commands = [a[2:] for a in argv if a.startswith("-e")]
    if "-c" in argv:
        if os.environ.get("FAKE_CHDK_FAIL") == "connect":
            print("ERROR: no matching devices found")
            return 1
        print("connected: Canon EOS M3 (fake)")
    for cmd in commands:
        if cmd.startswith("="):
            if "get_temperature" in cmd:
                print(f"1:return:{os.environ.get('FAKE_CHDK_TEMP', '21')}")
        elif cmd in ("rec", "play"):
            pass
        elif cmd.startswith("rs "):
            _remoteshoot(shlex.split(cmd)[1:])
        else:
            print(f"ERROR: unknown command {cmd!r}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
