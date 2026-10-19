"""Alpaca discovery responder (IPv4 UDP broadcast, protocol version 1)."""

import json
import logging
import socket
import threading
import time

log = logging.getLogger(__name__)

DISCOVERY_MESSAGE = b"alpacadiscovery1"
MIN_REPLY_INTERVAL_S = 0.05  # per sender; ignores floods, real clients ask a few times at most


class DiscoveryResponder:
    def __init__(self, alpaca_port: int, discovery_port: int = 32227, bind: str = "0.0.0.0"):
        self.reply = json.dumps({"AlpacaPort": alpaca_port}).encode()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # other Alpaca servers (e.g. the ASCOM OmniSimulator) may share the discovery port
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, discovery_port))
        self.port = self.sock.getsockname()[1]
        self._closed = False
        self._last_reply: dict[tuple, float] = {}
        self._thread = threading.Thread(target=self._serve, name="alpaca-discovery", daemon=True)

    def start(self) -> "DiscoveryResponder":
        self._thread.start()
        log.info("discovery responder on UDP %d", self.port)
        return self

    def close(self) -> None:
        self._closed = True
        self.sock.close()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _serve(self) -> None:
        while not self._closed:
            try:
                data, addr = self.sock.recvfrom(1024)
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" from an earlier reply (the client closed its
                # socket before our answer arrived) on the next receive; it says nothing about this socket
                continue
            except OSError as e:
                if self._closed:
                    return
                log.warning("discovery receive error (continuing): %s", e)
                time.sleep(0.1)
                continue
            if data.strip() != DISCOVERY_MESSAGE:
                continue
            now = time.monotonic()
            if now - self._last_reply.get(addr, 0.0) < MIN_REPLY_INTERVAL_S:
                continue
            self._last_reply[addr] = now
            if len(self._last_reply) > 1024:  # forget old senders
                self._last_reply = {a: t for a, t in self._last_reply.items() if now - t < 60}
            log.debug("discovery request from %s", addr)
            try:
                self.sock.sendto(self.reply, addr)
            except OSError as e:
                if self._closed:
                    return
                log.debug("discovery reply to %s failed: %s", addr, e)
