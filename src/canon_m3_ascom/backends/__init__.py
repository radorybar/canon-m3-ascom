from ..config import Config
from .base import Backend, BackendError


def create_backend(cfg: Config) -> Backend:
    if cfg.camera.backend == "sim":
        from .sim import SimBackend
        return SimBackend(cfg)
    if cfg.camera.backend == "ptp":
        from .ptp import PtpBackend
        return PtpBackend(cfg)
    from .chdk import ChdkBackend
    return ChdkBackend(cfg)


__all__ = ["Backend", "BackendError", "create_backend"]
