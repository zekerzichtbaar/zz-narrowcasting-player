"""WebSocket link to the CMS (docs/protocol.md section 4): update signal in, hello/heartbeat out.

Runs an asyncio loop in a daemon thread (Bookworm ships websockets 10.4: no sync client).
Works with websockets 10.4 (legacy API) and >= 14 (new asyncio API).
Self-check: python3 -m pi_player.ws
"""
import asyncio
import json
import logging
import os
import queue
import random
import socket
import threading
import time
import urllib.parse

from . import __version__
from .pairing import device_id

log = logging.getLogger("pi-player")

HEARTBEAT_EVERY = 30.0
NO_TOKEN_RECHECK = 5.0

# Shared with the play loop (plain dict, single-key writes are atomic enough under the GIL).
STATUS = {"state": "WAITING", "drift_ms": None}


# ---------- pure helpers ----------

def backoff(attempt, rng=random.random):
    """Delay before reconnect attempt n (0-based): 1, 2, 4 ... capped at 60 s, +-20 % jitter."""
    base = min(60.0, 2.0 ** attempt)
    return base * (0.8 + 0.4 * rng())


def ws_url(cms_url):
    u = urllib.parse.urlsplit(cms_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss"}.get(u.scheme, u.scheme)
    return urllib.parse.urlunsplit((scheme, u.netloc, u.path + "/api/devices/ws", "", ""))


def heartbeat_payload(device_id, now, state, version, pending_version, drift_ms, uptime, ip, network):
    return {
        "type": "heartbeat",
        "device_id": device_id,
        "time": round(now, 3),
        "state": state,
        "version": version,
        "pending_version": pending_version,
        "drift_ms": None if drift_ms is None or state != "PLAYING" else round(drift_ms, 1),
        "uptime": int(uptime),
        "ip": ip,
        "network": network,
    }


def close_code(exc):
    """Close code from a ConnectionClosed (10.4 and new API both have .rcvd)."""
    rcvd = getattr(exc, "rcvd", None)
    return rcvd.code if rcvd is not None else None


def handshake_status(exc):
    """HTTP status of a rejected upgrade: InvalidStatusCode (10.4) / InvalidStatus (new)."""
    if hasattr(exc, "status_code"):
        return exc.status_code
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


# ---------- best-effort host info ----------

def local_ip(cms_url):
    host = urllib.parse.urlsplit(cms_url).hostname or "8.8.8.8"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))  # UDP connect sends nothing; just picks the route
            return s.getsockname()[0]
    except OSError:
        return None


def network_kind(sys_net="/sys/class/net"):
    """'ethernet' if a wired iface is up, else 'wifi' if a wireless one is, else None."""
    try:
        names = os.listdir(sys_net)
    except OSError:
        return None
    up = {}
    for n in names:
        try:
            with open(os.path.join(sys_net, n, "operstate")) as f:
                if f.read().strip() != "up":
                    continue
        except OSError:
            continue
        up[n] = "wifi" if os.path.exists(os.path.join(sys_net, n, "wireless")) else "ethernet"
    kinds = {k for n, k in up.items() if n != "lo"}
    return "ethernet" if "ethernet" in kinds else ("wifi" if "wifi" in kinds else None)


# ---------- runtime ----------

class Link:
    """Owns the socket. Thread-safe API for other threads: send(msg)."""

    def __init__(self, cfg, content):
        self.cfg = cfg
        self.content = content
        self.out = queue.Queue()
        self.started = time.time()
        self.device_id = device_id()

    def start(self):
        threading.Thread(target=lambda: asyncio.run(self._main()), name="ws", daemon=True).start()

    def send(self, msg):
        self.out.put(msg)

    def download_failed(self, version, reason):
        self.send({"type": "download_failed", "version": version, "reason": reason})

    def _status(self):
        cur = self.content.current_manifest()
        return (STATUS["state"], cur and cur["version"], self.content.pending_version())

    def _heartbeat(self):
        state, version, pending = self._status()
        return heartbeat_payload(self.device_id, time.time(), state, version, pending, STATUS["drift_ms"],
                                 time.time() - self.started, local_ip(self.cfg["cms_url"]), network_kind())

    async def _connect(self, url, token):
        import websockets
        headers = {"Authorization": f"Bearer {token}"}
        try:
            from websockets.asyncio.client import connect  # websockets >= 13
            return await connect(url, additional_headers=headers, open_timeout=15)
        except ImportError:
            return await websockets.connect(url, extra_headers=headers, open_timeout=15)

    async def _main(self):
        attempt = 0
        url = ws_url(self.cfg["cms_url"])
        while True:
            token = self.content.token()
            if not token:
                await asyncio.sleep(NO_TOKEN_RECHECK)
                continue
            try:
                ws = await self._connect(url, token)
            except Exception as e:
                if handshake_status(e) == 401:
                    self._unpaired("ws handshake 401")
                    continue
                delay = backoff(attempt)
                attempt += 1
                log.warning("ws connect to %s failed (%s); retry in %.1f s", url, e, delay)
                await asyncio.sleep(delay)
                continue
            attempt = 0
            log.info("ws connected to %s", url)
            try:
                await self._session(ws)
            except Exception as e:
                code = close_code(e)
                if code == 4401:
                    self._unpaired("ws close 4401")
                    continue
                log.warning("ws closed (%s)", e)
            finally:
                await ws.close()
            if not self.content.token():
                continue
            delay = backoff(0)
            log.info("ws reconnect in %.1f s", delay)
            await asyncio.sleep(delay)

    async def _session(self, ws):
        cur = self.content.current_manifest()
        await ws.send(json.dumps({"type": "hello", "device_id": self.device_id,
                                  "agent_version": __version__, "version": cur and cur["version"]}))
        sender = asyncio.ensure_future(self._sender(ws))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                t = msg.get("type") if isinstance(msg, dict) else None
                if t == "screen_updated" and isinstance(msg.get("screen"), dict):
                    self.content.set_screen(msg["screen"])
                if t in ("update_available", "screen_updated"):
                    log.info("ws: %s %s -> fetching manifest", t, msg.get("version", ""))
                    self.content.poke()
                elif t == "unpaired":
                    self._unpaired("ws unpaired message")
                    return
            # normal close (1000/1001); other codes, incl. 4401, raise ConnectionClosedError
            log.info("ws closed by server")
        finally:
            sender.cancel()

    async def _sender(self, ws):
        last_key, last_sent = None, 0.0
        while True:
            if not self.content.token():  # pi-player reset / pairing lost: drop the session
                await ws.close()
                return
            while True:
                try:
                    await ws.send(json.dumps(self.out.get_nowait()))
                except queue.Empty:
                    break
            key = self._status()
            if key != last_key or time.monotonic() - last_sent >= HEARTBEAT_EVERY:
                await ws.send(json.dumps(self._heartbeat()))
                last_key, last_sent = key, time.monotonic()
            await asyncio.sleep(1.0)

    def _unpaired(self, why):
        log.warning("unpaired (%s): deleting token, content fetching stops", why)
        self.content.forget_token()


# ---------- self-check ----------

def _selfcheck():
    import tempfile
    # backoff: 1, 2, 4 ... 60 cap, jitter within +-20 %
    assert [backoff(n, lambda: 0.5) for n in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert backoff(0, lambda: 0.0) == 0.8 and abs(backoff(10, lambda: 1.0) - 72.0) < 1e-9
    assert ws_url("http://cms.local:8080") == "ws://cms.local:8080/api/devices/ws"
    assert ws_url("https://cms.example.com/base/") == "wss://cms.example.com/base/api/devices/ws"
    hb = heartbeat_payload("pi-1", 100.12345, "PLAYING", "v1", None, 3.14159, 12.9, "10.0.0.2", "wifi")
    assert hb == {"type": "heartbeat", "device_id": "pi-1", "time": 100.123, "state": "PLAYING",
                  "version": "v1", "pending_version": None, "drift_ms": 3.1, "uptime": 12,
                  "ip": "10.0.0.2", "network": "wifi"}
    assert heartbeat_payload("pi-1", 0, "WAITING", None, "v2", 5.0, 0, None, None)["drift_ms"] is None
    class C(Exception):
        rcvd = type("R", (), {"code": 4401})()
    assert close_code(C()) == 4401 and close_code(ValueError()) is None

    class E1(Exception):
        status_code = 401

    class E2(Exception):
        response = type("R", (), {"status_code": 401})()
    assert handshake_status(E1()) == 401 and handshake_status(E2()) == 401
    assert handshake_status(OSError()) is None
    d = tempfile.mkdtemp()
    for name, state, wireless in (("lo", "unknown", False), ("eth0", "down", False), ("wlan0", "up", True)):
        os.makedirs(os.path.join(d, name))
        open(os.path.join(d, name, "operstate"), "w").write(state + "\n")
        if wireless:
            os.makedirs(os.path.join(d, name, "wireless"))
    assert network_kind(d) == "wifi"
    open(os.path.join(d, "eth0", "operstate"), "w").write("up\n")
    assert network_kind(d) == "ethernet"
    assert network_kind(d + "/nope") is None
    print("ws self-check OK")


if __name__ == "__main__":
    _selfcheck()
