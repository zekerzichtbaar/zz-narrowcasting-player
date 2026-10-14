"""HTTP heartbeat to the CMS (docs/protocol.md section 4): POST /api/devices/heartbeat every 30 s
and right away on a state/version change. 401 = token revoked -> delete it (back to PAIRING).

Self-check: python3 -m pi_player.heartbeat
"""
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .pairing import device_id

log = logging.getLogger("pi-player")

HEARTBEAT_EVERY = 30.0
TICK = 1.0

# Shared with the play loop (plain dict, single-key writes are atomic enough under the GIL).
STATUS = {"state": "WAITING", "drift_ms": None}


class Unauthorized(Exception):
    """CMS answered 401: token revoked."""


# ---------- pure helpers ----------

def heartbeat_payload(device_id, now, state, version, pending_version, drift_ms, uptime, ip, network):
    return {
        "device_id": device_id,
        "agent_version": __version__,
        "time": round(now, 3),
        "state": state,
        "version": version,
        "pending_version": pending_version,
        "drift_ms": None if drift_ms is None or state != "PLAYING" else round(drift_ms, 1),
        "uptime": int(uptime),
        "ip": ip,
        "network": network,
    }


def due(key, last_key, since_last, every=HEARTBEAT_EVERY):
    """Send now? On any change of (state, version, pending_version), else every `every` seconds."""
    return key != last_key or since_last >= every


# ---------- I/O ----------

def send(cms_url, token, payload, timeout=15):
    """POST the heartbeat. Raises Unauthorized on 401; other errors propagate."""
    req = urllib.request.Request(cms_url.rstrip("/") + "/api/devices/heartbeat",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise Unauthorized() from e
        raise


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

class Heartbeat:
    """Background thread: while there is a token, report state + versions to the CMS."""

    def __init__(self, cfg, content):
        self.cfg = cfg
        self.content = content
        self.started = time.time()
        self.device_id = device_id()

    def start(self):
        threading.Thread(target=self._loop, name="heartbeat", daemon=True).start()

    def _status(self):
        cur = self.content.current_manifest()
        return (STATUS["state"], cur and cur["version"], self.content.pending_version())

    def _loop(self):
        last_key, last_sent = None, float("-inf")
        while True:
            token = self.content.token()
            key = self._status()
            if token and due(key, last_key, time.monotonic() - last_sent):
                state, version, pending = key
                payload = heartbeat_payload(self.device_id, time.time(), state, version, pending,
                                            STATUS["drift_ms"], time.time() - self.started,
                                            local_ip(self.cfg["cms_url"]), network_kind())
                # Counts as sent also on failure: retry at the next 30 s, not every tick.
                last_key, last_sent = key, time.monotonic()
                try:
                    send(self.cfg["cms_url"], token, payload)
                except Unauthorized:
                    log.warning("heartbeat: 401, token revoked; deleting token")
                    self.content.forget_token()
                    last_key = None
                except Exception as e:
                    log.warning("heartbeat failed: %s", e)
            elif not token:
                last_key = None  # first heartbeat after (re)pairing goes out right away
            time.sleep(TICK)


# ---------- self-check ----------

def _selfcheck():
    import http.server
    import tempfile
    hb = heartbeat_payload("pi-1", 100.12345, "PLAYING", "v1", None, 3.14159, 12.9, "10.0.0.2", "wifi")
    assert hb == {"device_id": "pi-1", "agent_version": __version__, "time": 100.123, "state": "PLAYING",
                  "version": "v1", "pending_version": None, "drift_ms": 3.1, "uptime": 12,
                  "ip": "10.0.0.2", "network": "wifi"}
    assert heartbeat_payload("pi-1", 0, "WAITING", None, "v2", 5.0, 0, None, None)["drift_ms"] is None
    k = ("PLAYING", "v1", None)
    assert due(k, None, 0) and not due(k, k, 29.9) and due(k, k, 30.0)
    assert due(("PLAYING", "v1", "v2"), k, 0)

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

    # send(): bearer + JSON body to /api/devices/heartbeat; 204 ok, 401 -> Unauthorized, 500 raises
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            got.append((self.path, self.headers["Authorization"], body))
            self.send_response({"Bearer good": 204, "Bearer gone": 401}.get(self.headers["Authorization"], 500))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/"
    send(base, "good", hb)
    assert got == [("/api/devices/heartbeat", "Bearer good", hb)]
    try:
        send(base, "gone", hb)
        raise AssertionError("expected Unauthorized")
    except Unauthorized:
        pass
    try:
        send(base, "other", hb)
        raise AssertionError("expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 500
    srv.shutdown()
    print("heartbeat self-check OK")


if __name__ == "__main__":
    _selfcheck()
