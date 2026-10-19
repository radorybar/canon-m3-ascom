"""Driver configuration (TOML). See config.example.toml for the documented defaults."""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def _default_frames_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return str(Path(base) / "canon-m3-ascom" / "frames")


BACKENDS = ("sim", "ptp", "chdk")


@dataclass
class ServerConfig:
    # Discovery replies come from whichever interface the request arrived on (e.g. a WSL/Hyper-V
    # adapter), and N.I.N.A. connects to that address, so listen on all interfaces by default.
    host: str = "0.0.0.0"
    port: int = 11112
    discovery: bool = True
    discovery_port: int = 32227
    imagebytes_uint16: bool = True  # send pixels as UInt16 (half the bytes); False -> Int32
    log_dir: str = ""  # "" = "logs" next to the config file (or %LOCALAPPDATA%\canon-m3-ascom\logs)


@dataclass
class CameraConfig:
    backend: str = "sim"  # "sim" | "ptp" (own CHDK client) | "chdk" (external chdkptp.exe)
    exposure_min: float = 1 / 4000
    exposure_max: float = 300.0  # verified on the M3 up to 120 s; CHDK allows up to 2048 s
    default_iso: int = 800
    frames_dir: str = field(default_factory=_default_frames_dir)


@dataclass
class ChdkConfig:
    command: list[str] = field(default_factory=lambda: ["chdkptp.exe"])
    format: str = "raw"  # "raw" (decoded here) — "dng" is planned once spike 2 confirms it
    timeout_extra_s: float = 30.0
    keep_files: bool = False
    lcd_off: bool = True
    prevent_shutdown: bool = True


@dataclass
class PtpConfig:
    transport: str = "auto"  # "auto" | "winusb" (Zadig/libwdi driver) | "wpd" (standard Windows driver)
    pid: int = 0  # USB product id; 0 = first Canon camera found
    lcd_off: bool = False  # turn the camera's LCD off (less heat) once connected
    prevent_shutdown: bool = True  # CHDK alt_prevent_shutdown = always
    dark_frame_off: bool = True  # CHDK raw noise reduction off (no automatic dark frame)
    # Send the DryOS "unlock" event (4482) when connecting. Needed whenever the standard Windows driver has
    # touched the camera since it was switched on (WPD transport, or right after a driver change); without
    # it remote capture fires the shutter but never delivers data. Verified harmless on the EOS M3.
    unlock: bool = True
    timeout_extra_s: float = 30.0
    keepalive_s: float = 60.0  # while idle, query the camera this often (temperature, link check); 0 = off
    keep_raw: bool = False  # also save every .raw buffer to camera.frames_dir
    fake: bool = False  # emulate the camera at the USB/PTP level (no hardware needed)
    fake_download_s: float = 3.0
    fake_time_scale: float = 1.0  # multiply emulated exposure times (tests use 0)
    fake_focuser_url: str = ""


@dataclass
class SimConfig:
    download_s: float = 3.0  # simulated USB transfer time
    roundtrip_codec: bool = True  # pack + unpack every frame, like the real raw path
    focuser_url: str = ""  # e.g. "http://127.0.0.1:32323/api/v1/focuser/0" (ASCOM OmniSimulator)
    best_focus: int = 5000
    stars: int = 1500
    temperature_c: float = 18.0


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    chdk: ChdkConfig = field(default_factory=ChdkConfig)
    ptp: PtpConfig = field(default_factory=PtpConfig)
    sim: SimConfig = field(default_factory=SimConfig)


def _apply(section, values: dict, name: str) -> None:
    known = {f.name for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"unknown config key [{name}].{key}")
        setattr(section, key, value)


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path is None:
        return cfg
    # utf-8-sig: Notepad / PowerShell 5.1 save UTF-8 with a BOM, which tomllib rejects
    data = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    for name in ("server", "camera", "chdk", "ptp", "sim"):
        if name in data:
            _apply(getattr(cfg, name), data.pop(name), name)
    if data:
        raise ValueError(f"unknown config sections: {', '.join(data)}")
    if cfg.camera.backend not in BACKENDS:
        raise ValueError(f"camera.backend must be one of {', '.join(BACKENDS)}")
    return cfg
