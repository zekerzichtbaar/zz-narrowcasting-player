"""Managing a paired screen over Bluetooth LE (the QR sticker): session, frames and ops.

Protocol: docs/ble-management.md. blesetup.py exports the GATT objects (one application with the
setup service) and calls the Manager on its GLib thread. scan/connect run on the wifi thread
(netman.WifiSetup); tick() picks up their outcome and seals it here, so frames and D-Bus signals
only ever happen on the GLib thread.

Self-check: python3 -m pi_player.blesetup (runs this module's checks too)
"""
import json
import logging
import os
import time

from .blesetup import BadCommand, Reassembler, connect_args

log = logging.getLogger("pi-player")

SERVICE_UUID = "8edd2620-ddcd-4435-a57e-98a6b4dd342f"
SESSION_UUID = "8edd2621-ddcd-4435-a57e-98a6b4dd342f"
REQUEST_UUID = "8edd2622-ddcd-4435-a57e-98a6b4dd342f"
RESPONSE_UUID = "8edd2623-ddcd-4435-a57e-98a6b4dd342f"
VERSION = 1
INFO = b"zz-manage-v1"
SALT = b"zz-narrowcasting"
MAX_VALUE = 512
OVERHEAD = 2 + 4 + 16
NONCE_LEN = 16
IDLE = 300.0
JOB_MAX = 150.0
REQUEST, RESPONSE = 1, 2
OPS = ["status", "scan", "connect"]
NAME_MAX = 64
MSG_MAX = 120


class Unauthorized(Exception):
    """ATT 0x08: no secret, no session, does not decrypt, stale ctr."""


class BadFrame(Exception):
    """ATT 0x0e: malformed frame or SESSION write."""


# --- pure protocol --------------------------------------------------------------------------

def session_key(secret, na, np):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=INFO + na + np).derive(secret.encode())


def _nonce(direction, ctr):
    return bytes([direction]) + bytes(7) + ctr.to_bytes(4, "big")


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def seal(key, direction, ctr, obj):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    body = ctr.to_bytes(4, "big") + AESGCM(key).encrypt(_nonce(direction, ctr), encode(obj),
                                                         INFO + bytes([direction]))
    return len(body).to_bytes(2, "big") + body


def unseal(key, direction, frame):
    """Frame -> (ctr, plaintext bytes). BadFrame: malformed. Unauthorized: not this key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(frame) < OVERHEAD or int.from_bytes(frame[:2], "big") != len(frame) - 2:
        raise BadFrame("bad frame length")
    ctr = int.from_bytes(frame[2:6], "big")
    try:
        return ctr, AESGCM(key).decrypt(_nonce(direction, ctr), frame[6:], INFO + bytes([direction]))
    except InvalidTag:
        raise Unauthorized("frame does not decrypt with this session's key")


def fits(obj):
    return len(encode(obj)) + OVERHEAD <= MAX_VALUE


def scan_rows(networks):
    return [[n["ssid"], int(n["signal"]), 1 if n["security"] else 0] for n in networks]


def fit_scan(resp):
    """Drop the weakest networks until the sealed frame fits in one ATT value."""
    rows = list(resp["networks"])
    while not fits(dict(resp, networks=rows)):
        rows.pop()
    return dict(resp, networks=rows)


def fit_status(resp):
    """A long screen name or job message must not push the frame over the ATT limit."""
    if fits(resp):
        return resp
    job = resp.get("job")
    resp = dict(resp, name=(resp.get("name") or "")[:NAME_MAX] or None,
                job=job and dict(job, msg=(job.get("msg") or "")[:MSG_MAX] or None))
    return resp if fits(resp) else dict(resp, job=job and dict(job, msg=None))


def advert_kind(setup_window, has_secret):
    """Which service UUID to advertise (docs/ble-management.md, Advertising): one fits."""
    if setup_window:
        return "setup"
    return "manage" if has_secret else None


def status_fields(dev_id, content, monitor):
    """The device part of a `status` answer. nmcli/route lookups with short timeouts: this runs on
    the GLib thread."""
    from . import __version__, heartbeat, netman
    ssid, signal = netman.wifi_signal() if netman.available() else (None, None)
    return {"device": dev_id, "name": content.screen_name(), "agent_version": __version__,
            "state": heartbeat.STATUS["state"], "paired": bool(content.token()),
            "online": bool(monitor.ok) if monitor else None, "network": heartbeat.network_kind(),
            "ssid": ssid, "signal": signal, "ip": heartbeat.local_ip("http://8.8.8.8")}


# --- sessions -------------------------------------------------------------------------------

class Session:
    def __init__(self, secret, na, np, now):
        self.secret, self.np = secret, np
        self.key = session_key(secret, na, np)
        self.last_req = 0
        self.resp_ctr = 0
        self.response = b""
        self.active = now


class Job:
    """The last scan/connect from a phone. mode "setup": the setup service's job on the hotspot
    (mark = scan_id/attempts before it); "manage": netman.WifiSetup.manage() (mark = its job id)."""

    def __init__(self, op, ssid, mode, mark, session, device, re, now):
        self.op, self.ssid, self.mode, self.mark = op, ssid, mode, mark
        self.session, self.device, self.re, self.since = session, device, re, now
        self.state, self.msg = "busy", None

    def summary(self):
        return {"op": self.op, "state": self.state, "ssid": self.ssid, "msg": self.msg}


class Manager:
    """Management sessions per BLE connection (BlueZ's `device` option) and the ops.

    secret: callable -> the stored management secret or None (read fresh, so a rotation ends the
    sessions within a manifest poll). wifi: netman.WifiSetup or None. status: callable -> the
    device fields of a `status` answer. busy: callable -> another service's wifi job runs."""

    def __init__(self, secret, wifi=None, status=dict, busy=lambda: False, clock=time.monotonic,
                 rand=os.urandom):
        self.secret_fn, self.wifi, self.status_fn, self.busy = secret, wifi, status, busy
        self.clock, self.rand = clock, rand
        self.secret = secret()
        self.sessions = {}
        self.parts = {}
        self.job = None
        self.notify = lambda frame: None

    # GATT entry points (GLib thread)
    def session_write(self, device, offset, value):
        self._poll_secret()
        if not self.secret:
            raise Unauthorized("no management secret stored")
        if offset or len(value) != NONCE_LEN:
            raise BadFrame("SESSION takes 16 bytes")
        self.parts.pop(device, None)
        self.sessions[device] = Session(self.secret, bytes(value), self.rand(NONCE_LEN), self.clock())

    def session_read(self, device, offset=0):
        return (bytes([VERSION]) + self._session(device).np)[offset:]

    def request_write(self, device, offset, value):
        self._poll_secret()
        if not self.secret:
            raise Unauthorized("no management secret stored")
        try:
            frame = self.parts.setdefault(device, Reassembler()).write(offset, bytes(value))
        except BadCommand as e:
            raise BadFrame(str(e))
        if frame is None:
            return
        s = self._session(device)
        ctr, plain = unseal(s.key, REQUEST, frame)
        if ctr <= s.last_req:
            raise Unauthorized(f"request ctr {ctr} is not fresh")
        s.last_req, s.active = ctr, self.clock()
        self._handle(s, device, ctr, plain)

    def response_read(self, device, offset=0):
        return self._session(device).response[offset:]

    def end(self, device):
        """The BLE connection went away."""
        self.parts.pop(device, None)
        if self.sessions.pop(device, None):
            log.info("bluetooth management: session of %s ended", device)

    def tick(self):
        """Every refresh: secret changes, idle sessions, finished wifi jobs."""
        now = self.clock()
        self._poll_secret()
        for dev, s in list(self.sessions.items()):
            if now - s.active > IDLE:
                self.end(dev)
        self._settle(now)

    # internals
    def _poll_secret(self):
        cur = self.secret_fn()
        if cur != self.secret:
            if self.sessions:
                log.info("bluetooth management: secret %s, ending %d session(s)",
                         "changed" if cur else "deleted", len(self.sessions))
            self.sessions.clear()
            self.parts.clear()
            self.secret = cur

    def _session(self, device):
        s = self.sessions.get(device)
        if not s or s.secret != self.secret:
            raise Unauthorized("no session for this connection")
        return s

    def _respond(self, s, resp):
        s.resp_ctr += 1
        s.response = seal(s.key, RESPONSE, s.resp_ctr, resp)
        self.notify(s.response)

    def _final(self, s, re, op, ok, **fields):
        self._respond(s, {"re": re, "op": op, "ok": ok, "final": True, **fields})

    def _fail(self, s, re, op, error, msg=None, **fields):
        self._final(s, re, op, False, error=error, msg=msg, **fields)

    def _handle(self, s, device, ctr, plain):
        try:
            req = json.loads(plain)
        except ValueError:
            req = None
        op = req.get("op") if isinstance(req, dict) else None
        if not isinstance(op, str):
            return self._fail(s, ctr, op, "bad_request", "Ongeldig verzoek.")
        if op not in OPS:
            return self._fail(s, ctr, op, "unknown_op")
        if op == "status":
            st = {"re": ctr, "op": op, "ok": True, "final": True, "v": VERSION, "ops": OPS,
                  **self.status_fn(), "job": self.job and self.job.summary()}
            return self._respond(s, fit_status(st))
        if self.wifi is None:
            return self._fail(s, ctr, op, "no_wifi", "Deze speler heeft geen wifi.")
        if (self.job and self.job.state == "busy") or self.busy():
            return self._fail(s, ctr, op, "busy", "De speler is nog bezig met een vorige opdracht.")
        ssid = pw = None
        if op == "connect":
            ssid, pw, err = connect_args(req)
            if err:
                return self._fail(s, ctr, op, "bad_request", err)
        self._start(s, device, ctr, op, ssid, pw, bool(req.get("hidden")))

    def _start(self, s, device, ctr, op, ssid, pw, hidden):
        w, now = self.wifi, self.clock()
        w.touch()
        if w.wanted:  # the hotspot is up: the setup service's scan/connect, one wifi job for both
            mark = w.scan_id if op == "scan" else w.attempts
            if op == "scan":
                w.rescan()
            else:
                w.connect(ssid, pw, hidden=hidden)
            self.job = Job(op, ssid, "setup", mark, s, device, ctr, now)
        else:
            self.job = Job(op, ssid, "manage", w.manage(op, ssid, pw, hidden), s, device, ctr, now)
        log.info("bluetooth management: %s%s (%s)", op, f" {ssid!r}" if ssid else "", self.job.mode)
        msg = f"Verbinden met {ssid}…" if op == "connect" else "Netwerken zoeken…"
        self._respond(s, {"re": ctr, "op": op, "final": False, "state": "busy", "msg": msg})

    def _settle(self, now):
        job = self.job
        if not job or job.state != "busy":
            return
        res = self._outcome(job, now)
        if res is None:
            return
        job.state, job.msg = ("ok" if res["ok"] else "failed"), res.get("msg")
        log.info("bluetooth management: %s %s", job.op, job.state)
        s = self.sessions.get(job.device)
        if s is job.session:
            resp = {"re": job.re, "op": job.op, "ok": res.pop("ok"), "final": True, **res}
            self._respond(s, fit_scan(resp) if "networks" in resp else resp)

    def _outcome(self, job, now):
        """The final fields of a finished job (ok, error, msg, ...), None while it runs."""
        w = self.wifi
        if job.mode == "manage":
            r = w.mjob
            if not r or r["id"] != job.mark or r["state"] == "busy":
                return None
            if job.op == "scan" and r["state"] == "ok":
                return {"ok": True, "networks": scan_rows(r["networks"])}
            if r["state"] == "ok":
                return {"ok": True, "msg": r.get("msg"), "ssid": r["ssid"], "ip": r.get("ip"),
                        "online": r.get("online")}
            return {"ok": False, "error": r.get("error") or "failed", "msg": r.get("msg"),
                    "ssid": r.get("now")}
        if job.op == "scan":
            if w.scan_id != job.mark:
                return {"ok": True, "networks": scan_rows(w.networks)}
        elif w.attempts != job.mark or w.joined == job.ssid:
            if w.joined == job.ssid:
                return {"ok": True, "msg": None, "ssid": job.ssid, "ip": self.status_fn().get("ip"),
                        "online": True}
            return {"ok": False, "error": "failed", "msg": w.error, "ssid": None}
        if not w.wanted or now - job.since > JOB_MAX:
            return {"ok": False, "error": "failed", "msg": "Wifi-instelling gestopt.", "ssid": None}
        return None

    @property
    def locked(self):
        return not self.secret


# --- self-check -----------------------------------------------------------------------------

VECTOR_SECRET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
VECTOR_NA = bytes(range(16))
VECTOR_NP = bytes(range(16, 32))
VECTOR_KS = "58cb3bf3146d29f20289f4397eefeb0b283e00efab27c34056d96b894cbfa048"
VECTOR_REQUEST = "0023000000016d4aebd66f2afc6e5d9fc7246f979f09d6a1a87cbd131eea5cef3524343cb0"
VECTOR_RESPONSE = ("004d00000001e9c9fbace569fc061f9e768a3bcd4adf7f064c9c5bf019ac29e316286054658ff8bb44"
                   "cef05cac73d8c22bf66f10d8b2f7b44103a78bb7bedd0efa4219f3963c9eff9905b6022eeed5")


class _FakeWifi:
    def __init__(self):
        self.wanted = False
        self.scan_id = self.attempts = 0
        self.networks, self.joined, self.error = [], None, None
        self.mjob, self.queued, self.calls = None, 0, []

    def touch(self):
        pass

    def rescan(self):
        self.calls.append(("rescan",))

    def connect(self, ssid, pw, hidden=False):
        self.calls.append(("connect", ssid, pw, hidden))

    def manage(self, op, ssid=None, pw=None, hidden=False):
        self.queued += 1
        self.calls.append(("manage", op, ssid, pw, hidden))
        self.mjob = {"id": self.queued, "op": op, "ssid": ssid, "state": "busy"}
        return self.queued


class _App:
    """The phone's side of the protocol."""

    def __init__(self, mgr, secret, device="/dev_A", na=None):
        self.mgr, self.secret, self.device = mgr, secret, device
        self.na = na or os.urandom(NONCE_LEN)
        self.ctr = self.seen = 0
        self.key = None

    def handshake(self):
        self.mgr.session_write(self.device, 0, self.na)
        hello = self.mgr.session_read(self.device)
        assert hello[0] == VERSION and len(hello) == 17
        self.key, self.ctr, self.seen = session_key(self.secret, self.na, hello[1:]), 0, 0
        return self

    def frame(self, req, ctr=None):
        if ctr is None:
            self.ctr += 1
            ctr = self.ctr
        return seal(self.key, REQUEST, ctr, req)

    def send(self, req):
        self.mgr.request_write(self.device, 0, self.frame(req))
        return self.read()

    def read(self):
        ctr, plain = unseal(self.key, RESPONSE, self.mgr.response_read(self.device))
        assert ctr > self.seen, (ctr, self.seen)
        self.seen = ctr
        return json.loads(plain)


def _raises(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return True
    raise AssertionError(f"{fn.__name__}{a!r} did not raise {exc.__name__}")


def _selfcheck_vectors():
    assert session_key(VECTOR_SECRET, VECTOR_NA, VECTOR_NP).hex() == VECTOR_KS
    ks = bytes.fromhex(VECTOR_KS)
    assert seal(ks, REQUEST, 1, {"op": "status"}).hex() == VECTOR_REQUEST
    resp = {"re": 1, "op": "scan", "ok": True, "final": True, "networks": []}
    assert seal(ks, RESPONSE, 1, resp).hex() == VECTOR_RESPONSE
    assert unseal(ks, REQUEST, bytes.fromhex(VECTOR_REQUEST)) == (1, b'{"op":"status"}')
    _raises(Unauthorized, unseal, ks, RESPONSE, bytes.fromhex(VECTOR_REQUEST))  # direction is bound
    assert advert_kind(True, False) == advert_kind(True, True) == "setup"
    assert advert_kind(False, True) == "manage" and advert_kind(False, False) is None


def _selfcheck_sessions():
    t = [1000.0]
    store = {"s": None}
    rand = iter([VECTOR_NP] + [os.urandom(16) for _ in range(100)])
    w = _FakeWifi()
    status = {"device": "pi-10000000a1b2c3d4", "name": "Kantine links", "agent_version": "v0.9.7",
              "state": "PLAYING", "paired": True, "online": True, "network": "wifi", "ssid": "Kantine",
              "signal": 82, "ip": "192.168.1.42"}
    other = {"busy": False}
    m = Manager(lambda: store["s"], w, status=lambda: dict(status), busy=lambda: other["busy"],
                clock=lambda: t[0], rand=lambda n: next(rand))
    sent = []
    m.notify = sent.append
    dev = "/org/bluez/hci0/dev_AA"

    # no secret: locked, nothing to read
    assert m.locked
    _raises(Unauthorized, m.session_write, dev, 0, VECTOR_NA)
    _raises(Unauthorized, m.request_write, dev, 0, bytes.fromhex(VECTOR_REQUEST))
    _raises(Unauthorized, m.session_read, dev)
    store["s"] = VECTOR_SECRET
    m.tick()
    assert not m.locked
    _raises(Unauthorized, m.session_read, dev)  # read before any write
    _raises(Unauthorized, m.response_read, dev)
    _raises(Unauthorized, m.request_write, dev, 0, bytes.fromhex(VECTOR_REQUEST))  # no session yet
    _raises(BadFrame, m.session_write, dev, 0, VECTOR_NA[:15])
    _raises(BadFrame, m.session_write, dev, 1, VECTOR_NA)

    # the handshake with the vector's na/np gives the vector's key; its request frame is answered
    m.session_write(dev, 0, VECTOR_NA)
    assert m.session_read(dev) == b"\x01" + VECTOR_NP and m.session_read(dev, 5) == (b"\x01" + VECTOR_NP)[5:]
    assert m.sessions[dev].key.hex() == VECTOR_KS
    vec = bytes.fromhex(VECTOR_REQUEST)
    m.request_write(dev, 0, vec[:10])
    m.request_write(dev, 10, vec[10:])  # long write in parts
    ks = bytes.fromhex(VECTOR_KS)
    ctr, plain = unseal(ks, RESPONSE, m.response_read(dev))
    st = json.loads(plain)
    assert ctr == 1 and sent == [m.response_read(dev)]
    assert st == {"re": 1, "op": "status", "ok": True, "final": True, "v": 1, "ops": OPS, **status,
                  "job": None}, st

    # replay: the same frame again, ctr 0, a lower ctr -> 0x08
    _raises(Unauthorized, m.request_write, dev, 0, vec)
    _raises(Unauthorized, m.request_write, dev, 0, seal(ks, REQUEST, 0, {"op": "status"}))
    m.request_write(dev, 0, seal(ks, REQUEST, 5, {"op": "status"}))
    _raises(Unauthorized, m.request_write, dev, 0, seal(ks, REQUEST, 4, {"op": "status"}))
    assert unseal(ks, RESPONSE, m.response_read(dev))[0] == 2  # Pi counts its own responses

    # malformed frames -> 0x0e, and the session survives them
    _raises(BadFrame, m.request_write, dev, 0, b"\x00\x03abc")
    _raises(BadFrame, m.request_write, dev, 0, vec + b"x")
    _raises(BadFrame, m.request_write, dev, 600, b"x")
    m.request_write(dev, 0, seal(ks, REQUEST, 6, {"op": "status"}))

    # decrypts but no valid request: accepted (ctr used up), answered encrypted
    for ctr, req, err in ((7, "not json", "bad_request"), (8, {"op": 5}, "bad_request"),
                          (9, {"op": "reboot"}, "unknown_op"), (10, [1], "bad_request")):
        m.request_write(dev, 0, seal(ks, REQUEST, ctr, req) if not isinstance(req, str) else
                        _seal_raw(ks, ctr, req.encode()))
        r = json.loads(unseal(ks, RESPONSE, m.response_read(dev))[1])
        assert r["re"] == ctr and r["ok"] is False and r["final"] and r["error"] == err, r
        _raises(Unauthorized, m.request_write, dev, 0, seal(ks, REQUEST, ctr, {"op": "status"}))

    # a captured connect frame: never twice in its session, never in a later one, and no wifi job
    grab = seal(ks, REQUEST, 11, {"op": "connect", "ssid": "Gast", "password": "geheim123"})
    m.request_write(dev, 0, grab)
    assert len(w.calls) == 1 and w.mjob["state"] == "busy"
    w.mjob = dict(w.mjob, state="ok", ip="192.168.1.57", online=True)
    m.tick()
    _raises(Unauthorized, m.request_write, dev, 0, grab)
    app = _App(m, VECTOR_SECRET, dev).handshake()  # a new SESSION write replaces the old session
    _raises(Unauthorized, m.request_write, dev, 0, grab)
    assert len(w.calls) == 1 and w.queued == 1
    assert app.send({"op": "status"})["re"] == 1

    # a wrong or old `s`: the handshake works (nothing readable), every request is 0x08
    bad = _App(m, "x" * 43, "/dev_B").handshake()
    _raises(Unauthorized, bad.send, {"op": "status"})
    _raises(Unauthorized, bad.send, {"op": "scan"})

    # two phones at once: own sessions, own counters, notify carries the latest frame of either
    two = _App(m, VECTOR_SECRET, "/dev_C").handshake()
    assert two.send({"op": "status"})["re"] == 1 and app.send({"op": "status"})["re"] == 2

    # rotation ends every session at once (also between ticks: checked on each request)
    store["s"] = "N" * 43
    _raises(Unauthorized, app.send, {"op": "status"})
    _raises(Unauthorized, m.response_read, "/dev_C")
    assert not m.sessions
    app = _App(m, "N" * 43, dev).handshake()
    assert app.send({"op": "status"})["ok"]
    store["s"] = None
    m.tick()
    assert m.locked and not m.sessions
    _raises(Unauthorized, app.send, {"op": "status"})
    _raises(Unauthorized, m.session_write, dev, 0, VECTOR_NA)
    store["s"] = VECTOR_SECRET

    # idle: 300 s without an accepted request ends the session; disconnect ends it at once
    app = _App(m, VECTOR_SECRET, dev).handshake()
    t[0] += IDLE - 1
    m.tick()
    assert app.send({"op": "status"})["ok"]
    t[0] += IDLE + 1
    m.tick()
    _raises(Unauthorized, app.send, {"op": "status"})
    app.handshake()
    m.end(dev)
    _raises(Unauthorized, app.send, {"op": "status"})
    return m, w, store, t, other


def _seal_raw(key, ctr, plain):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    body = ctr.to_bytes(4, "big") + AESGCM(key).encrypt(_nonce(REQUEST, ctr), plain, INFO + bytes([REQUEST]))
    return len(body).to_bytes(2, "big") + body


def _selfcheck_ops(m, w, store, t, other):
    dev = "/dev_ops"
    app = _App(m, VECTOR_SECRET, dev).handshake()
    sent = []
    m.notify = sent.append

    # scan outside the setup window: the wifi thread's management job, progress then final
    r = app.send({"op": "scan"})
    assert r == {"re": 1, "op": "scan", "final": False, "state": "busy", "msg": "Netwerken zoeken…"}, r
    assert w.calls[-1] == ("manage", "scan", None, None, False)
    assert app.send({"op": "scan"})["error"] == "busy"  # one job at a time
    assert app.send({"op": "connect", "ssid": "Gast", "password": "geheim123"})["error"] == "busy"
    st = app.send({"op": "status"})  # status is always allowed
    assert st["ok"] and st["job"] == {"op": "scan", "state": "busy", "ssid": None, "msg": None}, st
    latest = m.response_read(dev)
    m.tick()
    assert m.response_read(dev) == latest  # nothing finished yet: no new frame
    many = [{"ssid": f"Netwerk met een lange naam {i:02d}", "signal": 90 - i, "security": "WPA2"}
            for i in range(40)]
    w.mjob = dict(w.mjob, state="ok", networks=many)
    n = len(sent)
    m.tick()
    assert len(sent) == n + 1 and len(sent[-1]) <= MAX_VALUE
    r = app.read()
    assert r["re"] == 1 and r["ok"] and r["final"] and r["networks"][0] == ["Netwerk met een lange naam 00", 90, 1]
    assert 5 < len(r["networks"]) < 40, len(r["networks"])
    m.tick()
    assert len(sent) == n + 1  # one final per request
    assert app.send({"op": "status"})["job"]["state"] == "ok"

    # connect: validation first (bad_request with the portal's message), then the job
    r = app.send({"op": "connect", "ssid": "Gast", "password": "kort"})
    assert r["error"] == "bad_request" and r["msg"] and r["final"], r
    r = app.send({"op": "connect", "ssid": "Gast", "password": "geheim123"})
    assert r["state"] == "busy" and r["msg"] == "Verbinden met Gast…" and not r["final"], r
    assert w.calls[-1] == ("manage", "connect", "Gast", "geheim123", False)
    re = r["re"]
    w.mjob = dict(w.mjob, state="failed", error="failed", now="Kantine",
                  msg="Verbinden met Gast mislukt: Verkeerd wachtwoord?")
    m.tick()
    r = app.read()
    assert r == {"re": re, "op": "connect", "ok": False, "final": True, "error": "failed",
                 "msg": "Verbinden met Gast mislukt: Verkeerd wachtwoord?", "ssid": "Kantine"}, r
    st = app.send({"op": "status"})
    assert st["job"] == {"op": "connect", "state": "failed", "ssid": "Gast",
                         "msg": "Verbinden met Gast mislukt: Verkeerd wachtwoord?"}, st
    r = app.send({"op": "connect", "ssid": "Gast", "password": "geheim1234", "hidden": True})
    assert w.calls[-1] == ("manage", "connect", "Gast", "geheim1234", True)
    w.mjob = dict(w.mjob, state="ok", ip="192.168.1.57", online=True, msg=None)
    m.tick()
    r = app.read()
    assert r == {"re": r["re"], "op": "connect", "ok": True, "final": True, "msg": None, "ssid": "Gast",
                 "ip": "192.168.1.57", "online": True}, r

    # the setup service's wifi job runs: busy for management too
    other["busy"] = True
    assert app.send({"op": "scan"})["error"] == "busy"
    other["busy"] = False

    # the phone that asked went away: no frame, but `job` keeps the outcome
    app.send({"op": "scan"})
    m.end(dev)
    n = len(sent)
    w.mjob = dict(w.mjob, state="ok", networks=[])
    m.tick()
    assert len(sent) == n and m.job.state == "ok"

    # inside the setup window (hotspot up): the setup scan/connect, same wifi job
    app = _App(m, VECTOR_SECRET, dev).handshake()
    w.wanted, w.calls = True, []
    assert app.send({"op": "scan"})["final"] is False and w.calls == [("rescan",)]
    latest = m.response_read(dev)
    m.tick()
    assert m.response_read(dev) == latest
    w.scan_id, w.networks = w.scan_id + 1, [{"ssid": "Kantine", "signal": 82, "security": "WPA2"}]
    m.tick()
    assert app.read()["networks"] == [["Kantine", 82, 1]]
    app.send({"op": "connect", "ssid": "Kantine", "password": "geheim123"})
    assert w.calls[-1] == ("connect", "Kantine", "geheim123", False)
    w.attempts, w.error = w.attempts + 1, "Verbinden met Kantine mislukt: Verkeerd wachtwoord?"
    m.tick()
    r = app.read()
    assert not r["ok"] and r["msg"] == w.error, r
    app.send({"op": "connect", "ssid": "Kantine", "password": "geheim1234"})
    w.joined = "Kantine"  # joined is set before attempts on the wifi thread
    m.tick()
    r = app.read()
    assert r["ok"] and r["ssid"] == "Kantine" and r["ip"] == "192.168.1.42", r
    app.send({"op": "scan"})
    w.wanted = False  # the window closed under it (CMS "Wifi-scherm stoppen")
    m.tick()
    assert app.read()["error"] == "failed"

    # no wifi device on this Pi
    m.wifi = None
    assert app.send({"op": "scan"})["error"] == "no_wifi"
    m.wifi = w

    # status stays within one ATT value with a long name and job message
    m.status_fn = lambda: {"device": "pi-1", "name": "N" * 300}
    m.job.msg = "M" * 400
    n = len(sent)
    st = app.send({"op": "status"})
    assert len(sent[n]) <= MAX_VALUE and st["name"] == "N" * NAME_MAX, st


def _selfcheck():
    _selfcheck_vectors()
    _selfcheck_ops(*_selfcheck_sessions())
    print("blemanage self-check OK")


if __name__ == "__main__":
    _selfcheck()
