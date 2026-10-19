"""Camera-side Lua run by the PTP backend.

Modelled on chdkptp's rs_init / rlib_shoot_common (lua/rlibs.lua). Every script returns a single
string 'ok|...' or 'err|reason' so results never depend on how multiple return values are queued.
The first line tags the script for the USB-level camera emulator (ptp/fake.py).
"""

import math

TAG_SETUP = "--m3:setup"
TAG_CAPINIT = "--m3:capinit"
TAG_SHOOT = "--m3:shoot"
TAG_CAPDONE = "--m3:capdone"
TAG_TEMP = "--m3:temp"
TAG_INFO = "--m3:info"

# read-only camera facts for the diagnostic tool; changes nothing on the camera
INFO = f"""{TAG_INFO}
local b = get_buildinfo()
local support = 0
if type(get_usb_capture_support) == 'function' then support = get_usb_capture_support() end
local rec = get_mode()
return 'ok|' .. b.version .. '-' .. b.build_number .. '|' .. b.platform .. '-' .. b.platsub .. '|'
  .. get_temperature(1) .. '|' .. support .. '|' .. (rec and 'record' or 'playback')
"""


def tv96(seconds: float) -> int:
    """APEX*96 shutter value as CHDK uses it (chdkptp exp.shutter_to_tv96)."""
    return round(-96 * math.log2(seconds))


def sv96(iso: float) -> int:
    """APEX*96 speed value (chdkptp exp.iso_to_sv96)."""
    return round(96 * math.log2(iso / 3.125))


def _bool(v: bool) -> str:
    return "true" if v else "false"


def setup(lcd_off: bool, prevent_shutdown: bool, dark_frame_off: bool, unlock: bool = False) -> str:
    # unlock: when the standard Windows driver (WPD) owns the camera, Canon firmware can sit in a
    # "connected to PC" state where remote capture never delivers data; the DryOS logical event 4482
    # (CHDK wiki, PTP Extension FAQ) releases it. Verified on the EOS M3 over WPD.
    return f"""{TAG_SETUP}
if {_bool(unlock)} and type(post_levent_to_ui) == 'function' then post_levent_to_ui(4482) sleep(300) end
if not get_mode() then
  switch_mode_usb(1)
  local t0 = get_tick_count()
  while not get_mode() do
    sleep(50)
    if get_tick_count() - t0 > 5000 then return 'err|cannot switch to record mode' end
  end
end
if {_bool(prevent_shutdown)} then
  local ok, cnf = pcall(require, 'gen/cnf_core')
  if ok and type(cnf) == 'table' and cnf.alt_prevent_shutdown then set_config_value(cnf.alt_prevent_shutdown, 3) end
end
if {_bool(dark_frame_off)} and type(set_raw_nr) == 'function' then set_raw_nr(1) end
if {_bool(lcd_off)} then set_lcd_display(0) end
local support = 0
if type(get_usb_capture_support) == 'function' then support = get_usb_capture_support() end
return 'ok|' .. get_temperature(1) .. '|' .. support
"""


def capture_init(fmt: int, timeout_ms: int) -> str:
    return f"""{TAG_CAPINIT}
if not get_mode() then return 'err|camera is not in record mode' end
if type(init_usb_capture) ~= 'function' then return 'err|this CHDK build has no USB remote capture' end
if bitand(get_usb_capture_support(), {fmt}) ~= {fmt} then return 'err|raw remote capture not supported' end
if not init_usb_capture({fmt}, 0, 0) then return 'err|init_usb_capture failed' end
if type(set_usb_capture_timeout) == 'function' then set_usb_capture_timeout({timeout_ms}) end
return 'ok'
"""


def shoot(exposure_s: float, iso: int) -> str:
    # overrides must be set in the same script as the shot (CHDK wiki, PTP Extension / Other notes)
    return f"""{TAG_SHOOT}
set_tv96_direct({tv96(exposure_s)})
if type(sv96_market_to_real) == 'function' then set_sv96(sv96_market_to_real({sv96(iso)})) else set_sv96({sv96(iso)}) end
shoot()
return 'ok|' .. get_temperature(1)
"""


CAPTURE_DONE = f"""{TAG_CAPDONE}
init_usb_capture(0)
return 'ok'
"""

TEMPERATURE = f"""{TAG_TEMP}
return 'ok|' .. get_temperature(1)
"""


def parse_result(values: list) -> list[str]:
    """['ok|a|b'] -> ['a', 'b']; raises ValueError with the camera's reason on 'err|...'."""
    if not values or not isinstance(values[-1], str):
        raise ValueError(f"unexpected script result {values!r}")
    status, *fields = values[-1].split("|")
    if status != "ok":
        raise ValueError(fields[0] if fields else values[-1])
    return fields
