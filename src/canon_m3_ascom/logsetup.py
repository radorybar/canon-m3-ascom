"""Logging: console plus a rotating log file, so an unattended night can be analysed the next morning."""

import logging
import logging.handlers
import os
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_NAME = "canon-m3-ascom.log"


def default_log_dir(config_path: str | None) -> Path:
    """Next to the configuration file if there is one, else %LOCALAPPDATA%\\canon-m3-ascom\\logs."""
    if config_path:
        return Path(config_path).resolve().parent / "logs"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "canon-m3-ascom" / "logs"


def setup_logging(level: str = "INFO", log_dir: str | Path | None = None, console: bool = True) -> Path | None:
    """Configure the root logger; returns the log file path (None if the directory is not writable)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    logging.getLogger("comtypes").setLevel(logging.WARNING)  # its type-library cache chatter is not useful
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter(FORMAT)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    path = None
    if log_dir is not None:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            path = Path(log_dir) / LOG_NAME
            fh = logging.handlers.RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5,
                                                      encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as e:
            logging.warning("no log file (%s): %s", log_dir, e)
            path = None
    return path
