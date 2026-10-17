"""Alpaca discovery responder (IPv4 UDP broadcast, protocol version 1)."""

import json
import logging
import socket
import threading

log = logging.getLogger(__name__)

DISCOVERY_MESSAGE = b"alpacadiscovery1"


class DiscoveryResponder:
    def __init__(self, alpaca_port: int, discovery_port: int = 32227, bind: str = "0.0.0.0"):
        self.reply = json.dumps({"AlpacaPort": alpaca_port}).encode()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # other Alpaca servers (e.g. the ASCOM OmniSimulator) may share the discovery port
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, discovery_port))
        self.port = self.sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="alpaca-discovery", daemon=True)

    def start(self) -> "DiscoveryResponder":
        self._thread.start()
        log.info("discovery responder on UDP %d", self.port)
        return self

    def close(self) -> None:
        self.sock.close()

    def _serve(self) -> None:
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except OSError:
                return  # socket closed
            if data.startswith(DISCOVERY_MESSAGE):
                log.debug("discovery request from %s", addr)
                self.sock.sendto(self.reply, addr)
