"""Read a focuser position over Alpaca, so simulated stars blur with the (simulated) focuser."""

import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def read_position(focuser_url: str, timeout: float = 2.0) -> int | None:
    if not focuser_url:
        return None
    try:
        with urllib.request.urlopen(f"{focuser_url.rstrip('/')}/position", timeout=timeout) as r:
            body = json.load(r)
        if body.get("ErrorNumber"):
            log.warning("focuser error: %s", body.get("ErrorMessage"))
            return None
        return int(body["Value"])
    except (OSError, ValueError, KeyError) as e:
        log.warning("cannot read focuser position from %s: %s", focuser_url, e)
        return None
