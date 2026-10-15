"""The bel (docs/protocol.md section 6): a Pusher-protocol WebSocket (Laravel Reverb) on this
screen's private channel. Only a signal: on `poll` or `identify` the content thread fetches the
manifest over HTTP right away (Content.poke()) instead of at the next 30 s poll, so the manifest
stays the single source of truth. Without a bel (CMS has no Reverb, Reverb down) the 30 s poll
just carries on.

Stdlib only: a WebSocket client for one channel of small text frames is a few dozen lines, and it
spares an apt package whose API differs between Bookworm (websockets 10.4) and later releases.

Self-check (handshake, auth, ping/pong, reconnect with backoff, poll/identify against mock_cms.py):
python3 -m pi_player.bell
"""
import base64
import hashlib
import json
import logging
import os
import random
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

log = logging.getLogger("pi-player")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PONG_TIMEOUT = 10.0       # after our own ping: no frame within this = dead connection
NO_BELL_RECHECK = 300.0   # CMS without Reverb (204): ask again this much later
NO_TOKEN_RECHECK = 5.0    # not paired: look for a token again
TICK = 1.0                # how often the read loop checks for a stop or a new token


class Closed(Exception):
    """The WebSocket is gone (close frame, EOF, failed handshake)."""


# ---------- pure helpers ----------

def backoff(attempt, rng=random.random):
    """Seconds before reconnect attempt n (0-based): 1, 2, 4 ... capped at 60, +-25 % jitter so a
    fleet does not reconnect in step (same as the Chromium player's bell.ts)."""
    return min(60.0, 2.0 ** max(0, attempt)) * (0.75 + 0.5 * rng())


def socket_url(bell, cms_url):
    """ws(s)://host:port/app/{key}?protocol=7... ; host null = the CMS host."""
    host = bell.get("host") or urllib.parse.urlsplit(cms_url).hostname
    scheme = "wss" if bell.get("scheme") == "https" else "ws"
    return (f"{scheme}://{host}:{int(bell['port'])}/app/{urllib.parse.quote(bell['key'], safe='')}"
            f"?protocol=7&client=pi-player&version={__version__}&flash=false")


def accept_key(key):
    """Sec-WebSocket-Accept for a Sec-WebSocket-Key (RFC 6455 4.2.2)."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def encode_frame(payload, opcode=1, mask=None):
    """One final frame. Clients must mask (mask = 4 bytes), servers must not (mask = None)."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    bit = 0x80 if mask else 0
    if n < 126:
        head += bytes([bit | n])
    elif n < 1 << 16:
        head += bytes([bit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([bit | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def read_frame(read):
    """(fin, opcode, payload) of the next frame; read(n) returns exactly n bytes."""
    b0, b1 = read(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read(8))[0]
    mask = read(4) if b1 & 0x80 else None
    payload = read(n) if n else b""
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bool(b0 & 0x80), b0 & 0x0F, payload


def _data(data):
    """Pusher sends `data` as a JSON string (sometimes as an object)."""
    try:
        d = json.loads(data) if isinstance(data, str) else data
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def parse(raw, channel):
    """What a frame from Reverb means for the player: {"type": connected|subscribed|ping|pong|
    error|bell|other, ...}. Unknown or broken frames are `other`."""
    try:
        m = json.loads(raw)
    except ValueError:
        return {"type": "other"}
    if not isinstance(m, dict):
        return {"type": "other"}
    event, data = m.get("event"), _data(m.get("data"))
    if event == "pusher:connection_established" and isinstance(data.get("socket_id"), str):
        timeout = data.get("activity_timeout")
        ok = isinstance(timeout, (int, float)) and timeout > 0
        return {"type": "connected", "socket_id": data["socket_id"], "activity_timeout": float(timeout if ok else 30)}
    if event == "pusher_internal:subscription_succeeded" and m.get("channel") == channel:
        return {"type": "subscribed"}
    if event == "pusher:ping":
        return {"type": "ping"}
    if event == "pusher:pong":
        return {"type": "pong"}
    if event == "pusher:error":
        code = data.get("code")
        return {"type": "error", "code": code if isinstance(code, int) else None, "message": data.get("message")}
    if event in ("poll", "identify") and m.get("channel") == channel:
        return {"type": "bell", "event": event}
    return {"type": "other"}


# ---------- WebSocket client ----------

class WebSocket:
    """Blocking client for small text messages. recv(wait) returns a message, "" for a control
    frame, or None when nothing arrived within `wait` seconds."""

    def __init__(self, url, timeout=15.0):
        u = urllib.parse.urlsplit(url)
        secure = u.scheme == "wss"
        self.timeout = timeout
        self.buf = b""
        self.sock = socket.create_connection((u.hostname, u.port or (443 if secure else 80)), timeout)
        try:
            if secure:
                self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=u.hostname)
            key = base64.b64encode(os.urandom(16)).decode()
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\n"
                               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                               "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            while b"\r\n\r\n" not in self.buf:
                if len(self.buf) > 65536:
                    raise Closed("handshake: header too long")
                self._more(timeout)
            head, self.buf = self.buf.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            if lines[0].split(" ")[1:2] != ["101"]:
                raise Closed(f"handshake: {lines[0]}")
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
            if headers.get("sec-websocket-accept") != accept_key(key):
                raise Closed("handshake: bad Sec-WebSocket-Accept")
        except BaseException:
            self.sock.close()
            raise

    def _more(self, wait):
        self.sock.settimeout(wait)
        chunk = self.sock.recv(65536)
        if not chunk:
            raise Closed("connection closed")
        self.buf += chunk

    def _take(self, n):
        while len(self.buf) < n:
            self._more(self.timeout)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, payload, opcode=1):
        self.sock.settimeout(self.timeout)
        self.sock.sendall(encode_frame(payload, opcode, os.urandom(4)))

    def send_json(self, event, data):
        self.send(json.dumps({"event": event, "data": data}).encode())

    def recv(self, wait):
        if not self.buf:
            try:
                self._more(wait)
            except (socket.timeout, TimeoutError):
                return None
        message = b""
        while True:
            fin, opcode, payload = read_frame(self._take)
            if opcode == 8:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                raise Closed(f"close frame {code}")
            if opcode in (9, 10):  # control frames may come between fragments
                if opcode == 9:
                    self.send(payload, 10)
                if not message:
                    return ""
                continue
            if opcode in (0, 1):
                message += payload
                if fin:
                    return message.decode(errors="replace")
            # binary frames: not part of the Pusher protocol, ignored

    def close(self):
        try:
            self.sock.settimeout(1.0)
            self.sock.sendall(encode_frame(struct.pack(">H", 1000), 8, os.urandom(4)))
        except OSError:
            pass
        self.sock.close()


# ---------- CMS calls ----------

def fetch_config(cms_url, token, timeout=15):
    """GET /api/devices/bell: {key, host, port, scheme, channel}, or None (204: no Reverb)."""
    req = urllib.request.Request(cms_url.rstrip("/") + "/api/devices/bell",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return None if r.status == 204 else json.load(r)


def authorize(cms_url, token, socket_id, channel, timeout=15):
    """POST /api/broadcasting/auth -> the `auth` signature for pusher:subscribe. 403 raises."""
    req = urllib.request.Request(cms_url.rstrip("/") + "/api/broadcasting/auth",
                                 data=json.dumps({"socket_id": socket_id, "channel_name": channel}).encode(),
                                 method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        auth = json.load(r).get("auth")
    if not isinstance(auth, str):
        raise ValueError("no auth in the answer")
    return auth


# ---------- runtime ----------

class Bell:
    """Background thread: keep a subscription on this screen's channel while there is a token;
    every `poll`/`identify` (and every re-subscribe: a bel may have been missed) pokes the
    content thread, which coalesces pokes, so a burst never gives parallel manifest fetches."""

    def __init__(self, cfg, content, delay=backoff):
        self.cfg, self.content, self.delay = cfg, content, delay
        self.attempt = 0
        self.ever_subscribed = False
        self.subscribed = threading.Event()
        self.pong_timeout, self.no_bell_recheck = PONG_TIMEOUT, NO_BELL_RECHECK
        self._stop = threading.Event()
        self._ws = None

    def start(self):
        threading.Thread(target=self._loop, name="bell", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws:
            try:
                ws.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _loop(self):
        while not self._stop.is_set():
            token = self.content.token()
            if not token:
                self._stop.wait(NO_TOKEN_RECHECK)
                continue
            try:
                bell = fetch_config(self.cfg["cms_url"], token)
                if bell is None:
                    log.info("bell: CMS has none; polling only, asking again in %.0f s", self.no_bell_recheck)
                    self._stop.wait(self.no_bell_recheck)
                    continue
                self._session(bell, token)
            except Exception as e:  # network, 401/403, Reverb down, broken frames: retry later
                if not self._stop.is_set():
                    log.warning("bell: %s", e)
            if self._stop.is_set():
                break
            wait = self.delay(self.attempt)
            self.attempt += 1
            self._stop.wait(wait)

    def _session(self, bell, token):
        channel = bell["channel"]
        ws = self._ws = WebSocket(socket_url(bell, self.cfg["cms_url"]))
        try:
            activity, last, pinged = 30.0, time.monotonic(), None
            while not self._stop.is_set() and self.content.token() == token:
                raw = ws.recv(TICK)
                now = time.monotonic()
                if raw is None:
                    if pinged is not None and now - pinged > self.pong_timeout:
                        raise Closed("no pong")
                    if pinged is None and now - last > activity:
                        ws.send_json("pusher:ping", {})
                        pinged = now
                    continue
                last, pinged = now, None
                m = parse(raw, channel)
                if m["type"] == "connected":
                    activity = m["activity_timeout"]
                    auth = authorize(self.cfg["cms_url"], token, m["socket_id"], channel)
                    ws.send_json("pusher:subscribe", {"channel": channel, "auth": auth})
                elif m["type"] == "subscribed":
                    log.info("bell: listening on %s", channel)
                    self.attempt = 0
                    self.subscribed.set()
                    if self.ever_subscribed:
                        self.content.poke()
                    self.ever_subscribed = True
                elif m["type"] == "ping":
                    ws.send_json("pusher:pong", {})
                elif m["type"] == "error":
                    log.warning("bell: error %s %s", m["code"], m["message"])
                    if m["code"] is not None and 4000 <= m["code"] < 4100:
                        self.attempt = 10  # do not reconnect as is (wrong key, ...): longest backoff
                elif m["type"] == "bell":
                    log.info("bell: %s", m["event"])
                    self.content.poke()
        finally:
            self.subscribed.clear()
            self._ws = None
            ws.close()


# ---------- self-check ----------

def _selfcheck():
    import sys

    # backoff: 1, 2, 4 ... 60 with +-25 % jitter
    assert backoff(0, lambda: 0.5) == 1.0 and backoff(3, lambda: 0.5) == 8.0
    assert backoff(10, lambda: 0.5) == 60.0 and backoff(0, lambda: 0.0) == 0.75 and backoff(0, lambda: 1.0) == 1.25

    assert socket_url({"key": "k", "host": None, "port": 443, "scheme": "https"}, "https://cms.example/") == \
        f"wss://cms.example:443/app/k?protocol=7&client=pi-player&version={__version__}&flash=false"
    assert socket_url({"key": "k", "host": "ws.example", "port": 8080, "scheme": "http"}, "https://cms.example") \
        .startswith("ws://ws.example:8080/app/k?")

    # RFC 6455 examples
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    assert encode_frame(b"Hello") == bytes.fromhex("810548656c6c6f")
    masked = encode_frame(b"Hello", 1, bytes.fromhex("37fa213d"))
    assert masked == bytes.fromhex("818537fa213d7f9f4d5158")
    for frame, want in ((masked, b"Hello"), (encode_frame(b"x" * 300), b"x" * 300),
                        (encode_frame(b"y" * 70000, 1, b"abcd"), b"y" * 70000)):
        buf = [frame]

        def take(n, buf=buf):
            out, buf[0] = buf[0][:n], buf[0][n:]
            return out
        assert read_frame(take) == (True, 1, want) and buf[0] == b""

    ch = "private-screen.7"
    est = json.dumps({"event": "pusher:connection_established",
                      "data": json.dumps({"socket_id": "1.2", "activity_timeout": 120})})
    assert parse(est, ch) == {"type": "connected", "socket_id": "1.2", "activity_timeout": 120.0}
    assert parse(json.dumps({"event": "pusher_internal:subscription_succeeded", "channel": ch}), ch) == {"type": "subscribed"}
    assert parse(json.dumps({"event": "poll", "channel": ch, "data": "[]"}), ch) == {"type": "bell", "event": "poll"}
    assert parse(json.dumps({"event": "identify", "channel": ch, "data": {"identify_until": "x"}}), ch)["type"] == "bell"
    assert parse(json.dumps({"event": "poll", "channel": "private-screen.8", "data": "[]"}), ch) == {"type": "other"}
    assert parse(json.dumps({"event": "pusher:error", "data": {"code": 4001, "message": "nope"}}), ch) == \
        {"type": "error", "code": 4001, "message": "nope"}
    assert parse("not json", ch) == {"type": "other"} and parse("[1]", ch) == {"type": "other"}

    # ---- live against mock_cms.py (its bel speaks the Pusher protocol like Reverb) ----
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import mock_cms

    mock_cms.log = lambda *a: None
    srv = mock_cms.make_server(mock_cms.parse_args(["--bind", "127.0.0.1", "--port", "0"]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def admin(path):
        urllib.request.urlopen(urllib.request.Request(base + path, data=b"", method="POST"), timeout=5).read()

    def until(cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.02)
        return cond()

    class Stub:  # stands in for Content: token + poke
        def __init__(self, token):
            self.tok, self.pokes = token, 0

        def token(self):
            return self.tok

        def poke(self):
            self.pokes += 1

    attempts = []

    def fast(attempt):
        attempts.append(attempt)
        return 0.1

    logging.disable(logging.CRITICAL)
    stub = Stub("devtoken")
    admin("/admin/bell?activity_timeout=1")
    bell = Bell({"cms_url": base}, stub, delay=fast)
    bell.pong_timeout, bell.no_bell_recheck = 1.0, 0.3
    bell.start()
    assert until(bell.subscribed.is_set), "handshake + auth + subscribe"
    assert stub.pokes == 0 and mock_cms.BELL["connections"] == 1

    # poll and identify reach the content thread within 2 s
    t = time.monotonic()
    admin("/admin/identify?seconds=60")
    assert until(lambda: stub.pokes == 1, 2.0), "identify"
    admin("/admin/poll")
    assert until(lambda: stub.pokes == 2, 2.0) and time.monotonic() - t < 2.0, "poll"

    # quiet line: after the activity timeout the player pings and the server pongs; same connection
    pings = mock_cms.BELL["pings"]
    assert until(lambda: mock_cms.BELL["pings"] >= pings + 2, 4.0)
    assert bell.subscribed.is_set() and mock_cms.BELL["connections"] == 1

    # server drops the socket: reconnect after the backoff, re-subscribe and poll once (missed bel)
    admin("/admin/bell/drop")
    assert until(lambda: mock_cms.BELL["connections"] == 2 and bell.subscribed.is_set())
    assert until(lambda: stub.pokes == 3) and attempts == [0]

    # no pong: the player gives up on the line and reconnects
    admin("/admin/bell?pong=0")
    assert until(lambda: mock_cms.BELL["connections"] == 3, 6.0), "pong timeout"
    admin("/admin/bell?pong=1")
    assert until(bell.subscribed.is_set)

    # auth refused (403): no subscription, backoff grows; allowed again -> subscribed, backoff reset
    admin("/admin/bell?auth=0")
    del attempts[:]
    admin("/admin/bell/drop")
    assert until(lambda: len(attempts) >= 3) and attempts[:3] == [0, 1, 2] and not bell.subscribed.is_set()
    admin("/admin/bell?auth=1")
    assert until(bell.subscribed.is_set, 3.0)
    assert bell.attempt == 0

    # CMS without Reverb (204): no socket at all, polling only
    admin("/admin/bell?enabled=0")
    admin("/admin/bell/drop")
    conns = mock_cms.BELL["connections"]
    time.sleep(1.0)
    assert mock_cms.BELL["connections"] == conns and not bell.subscribed.is_set()
    admin("/admin/bell?enabled=1")
    assert until(bell.subscribed.is_set, 3.0)

    # a new token (paired again): the old subscription goes, the new one comes
    admin("/admin/pair?token=other")
    stub.tok = "other"
    assert until(lambda: mock_cms.BELL["connections"] == conns + 2 and bell.subscribed.is_set(), 4.0)

    bell.stop()
    srv.shutdown()
    logging.disable(logging.NOTSET)
    print("bell self-check OK")


if __name__ == "__main__":
    _selfcheck()
