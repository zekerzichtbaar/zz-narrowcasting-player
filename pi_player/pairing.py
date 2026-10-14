"""Identity + pairing (docs/protocol.md sections 1-2): register, poll status, store the token.

Self-check: python3 -m pi_player.pairing
"""
import json
import logging
import os
import socket
import threading
import time
import urllib.parse
import urllib.request

from . import __version__

log = logging.getLogger("pi-player")

REFRESH_BEFORE = 30.0  # re-register this long before the code expires
NO_TOKEN_IDLE = 2.0    # how often to look whether the token disappeared (unpaired)


def device_id():
    """Stable ID from the Pi serial (pi-<16 hex>); falls back to machine-id off-Pi."""
    for path, key in (("/proc/cpuinfo", "Serial"), ("/etc/machine-id", None)):
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            continue
        if key is None:
            return "pi-" + text.strip()[:16]
        for line in text.splitlines():
            if line.startswith(key):
                return "pi-" + line.split(":", 1)[1].strip()[-16:]
    return "pi-" + socket.gethostname()


def _post(url, body, timeout=15):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def register(cms_url, dev_id):
    """POST /api/devices/register -> {code, expires_at, pair_url, poll_interval}."""
    return _post(cms_url.rstrip("/") + "/api/devices/register", {
        "device_id": dev_id, "agent_version": __version__,
        "hostname": socket.gethostname(), "resolution": "1920x1080"})


def status(cms_url, dev_id, code, timeout=15):
    q = urllib.parse.urlencode({"device_id": dev_id, "code": code})
    with urllib.request.urlopen(cms_url.rstrip("/") + "/api/devices/register/status?" + q,
                                timeout=timeout) as r:
        return json.load(r)


def store_token(state_dir, token):
    """Write <state_dir>/token atomically with mode 0600 (never readable by others, not even briefly)."""
    path = os.path.join(state_dir, "token")
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def local_deadline(expires_at, now):
    """When to re-register, on our clock. Tolerates CMS/Pi clock skew: at least 60 s of display."""
    return now + max(60.0, float(expires_at) - now - REFRESH_BEFORE)


class Pairing:
    """Background thread: while there is no token, keep a valid code registered and poll for the
    claim. `info` (code/pair_url/expires_at, or None) is read by the main loop for the screen."""

    def __init__(self, cfg, content, dev_id=None):
        self.cfg = cfg
        self.content = content
        self.device_id = dev_id or device_id()
        self.info = None

    def start(self):
        threading.Thread(target=self._loop, name="pairing", daemon=True).start()

    def _loop(self):
        fails, deadline = 0, 0.0
        cms = self.cfg["cms_url"]
        while True:
            if self.content.token():
                self.info = None
                time.sleep(NO_TOKEN_IDLE)
                continue
            try:
                if self.info is None or time.time() >= deadline:
                    r = register(cms, self.device_id)
                    deadline = local_deadline(r["expires_at"], time.time())
                    self.info = {k: r.get(k) for k in ("code", "pair_url", "expires_at")}
                    self.info["poll_interval"] = float(r.get("poll_interval") or 3)
                    log.info("pairing code %s (%s)", r["code"], r.get("pair_url"))
                st = status(cms, self.device_id, self.info["code"])
                fails = 0
                if st.get("status") == "expired":
                    log.info("pairing code %s expired; registering a new one", self.info["code"])
                    self.info = None
                    continue
                if st.get("status") == "claimed" and st.get("device_token"):
                    store_token(self.content.dir, st["device_token"])
                    self.content.set_screen(st.get("screen"))
                    log.info("paired as %s", (st.get("screen") or {}).get("name"))
                    self.info = None
                    self.content.poke()  # fetch the manifest now, not at the next poll
                    continue
                time.sleep(self.info["poll_interval"])
            except Exception as e:
                delay = min(15.0, 2.0 ** fails)
                fails += 1
                log.warning("pairing: %s; retry in %.0f s", e, delay)
                time.sleep(delay)


def _selfcheck():
    import tempfile
    d = tempfile.mkdtemp()
    store_token(d, "dt_1")
    p = os.path.join(d, "token")
    assert open(p).read() == "dt_1\n" and (os.stat(p).st_mode & 0o777) == 0o600
    open(p + ".tmp", "w").write("stale")  # leftover from a crash mid-write
    store_token(d, "dt_2")
    assert open(p).read() == "dt_2\n" and (os.stat(p).st_mode & 0o777) == 0o600
    assert not os.path.exists(p + ".tmp")
    assert local_deadline(1600, 1000) == 1570       # normal: refresh 30 s before expiry
    assert local_deadline(900, 1000) == 1060        # CMS clock behind: still show it 60 s
    did = device_id()
    assert did.startswith("pi-") and len(did) > 3
    print("pairing self-check OK")


if __name__ == "__main__":
    _selfcheck()
