#!/usr/bin/env python3
"""Tiny mock CMS for local testing (see docs/protocol.md). Stdlib only.

  python3 mock_cms.py [--port 8080] [--token devtoken] [--media DIR] [--code-ttl 600]

Device endpoints:
  POST /api/devices/register     -> pairing code + poll_secret (invalidates the device's previous code)
  GET  /api/devices/register/status?device_id=&code=   bearer poll_secret; pending / expired /
                                 claimed (+token, once); a wrong or missing secret = expired
  GET  /api/devices/manifest     bearer token; 200 manifest / 204 nothing published / 401
  POST /api/devices/heartbeat    bearer token; logs it, 204 / 401
  GET  /api/devices/bell         bearer token; where the bel is (this server) / 204 when disabled / 401
  POST /api/broadcasting/auth    bearer token; {auth} for pusher:subscribe on the token's channel / 403
  GET  /app/<key>                the bel: a WebSocket speaking the Pusher protocol like Laravel Reverb
  GET  /media/<file>             the MP4s in --media
Admin (browser): GET /pair[?code=K7Q-4M2]  enter code + screen name/group -> device gets a token
Admin (curl):
  POST /admin/publish?file=a.mp4&delay=60[&duration=10][&epoch=0][&bad_sha=1][&wait=1][&orientation=portrait]
                                 wait=1: "waiting for the group" (activate_at 4102444800, epoch 0)
  POST /admin/activate?delay=45  group moment: same file, new version, activate_at = epoch = now+delay
  POST /admin/unpublish          back to 204
  POST /admin/identify[?seconds=60]   "Scherm identificeren": identify_until = now+seconds in every
                                 manifest (version unchanged; nothing published = 200 version null)
  POST /admin/rotate?rotation=90   how the screen hangs (0|90|270 clockwise), in every manifest; rings `poll`
  POST /admin/update[?version=v1.3.0][&url=...]   "Speler bijwerken": `update` in every manifest until a
                                 heartbeat reports that agent_version (no version = clear); rings `poll`
  POST /admin/unpair[?device_id=pi-...]   revoke tokens (all, or that device's): next call gets 401
  POST /admin/pair[?token=T]     re-enable the hand-provisioned --token (or add token T)
  POST /admin/poll               ring `poll` on the bel
  POST /admin/bell[?enabled=0|1][&auth=0|1][&pong=0|1][&activity_timeout=S]   bel switches
  POST /admin/bell/drop          close every bel socket (the device reconnects)
Devices pick changes up at their next manifest poll (default every 30 s); with the bel on, publish,
activate, unpublish, unpair and poll ring `poll` and identify rings `identify`, within a second.
"""
import argparse
import hashlib
import hmac
import html
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse

from pi_player.bell import accept_key, encode_frame, read_frame

STATE = {"manifest": None, "identify_until": 0, "rotation": 0, "update": None}
TOKENS = {}  # device token -> {"device_id", "screen"}; the --token one is added in main()
CODES = {}   # pairing code -> {"device_id", "expires_at", "token"|None}
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
LOCK = threading.Lock()
# The bel. Counters are for pi_player.bell's self-check.
BELL = {"enabled": True, "auth": True, "pong": True, "activity_timeout": 30, "connections": 0, "pings": 0}
BELL_KEY, BELL_SECRET = "mock-key", "mock-secret"
SOCKETS = []  # open bel connections: {"handler", "lock", "socket_id", "channel"|None}

PAIR_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pair a screen (mock CMS)</title>
<style>body{{font:16px system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}}
label{{display:block;margin:1rem 0 .3rem}}input{{font:inherit;width:100%;padding:.5rem;box-sizing:border-box}}
button{{font:inherit;margin-top:1.5rem;padding:.6rem 1.2rem}}.msg{{padding:.7rem;background:#eee}}</style>
</head><body><h1>Pair a screen</h1>{msg}
<form method="post" action="/pair">
<label>Pairing code</label><input name="code" value="{code}" autofocus required>
<label>Screen name</label><input name="name" value="Mock screen" required>
<label>Group</label><input name="group_id" value="grp_1" required>
<button>Pair</button></form>{pending}</body></html>"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def bearer(headers):
    a = headers.get("Authorization", "")
    return a[7:] if a.startswith("Bearer ") else None


def token_ok(headers):
    return bearer(headers) in TOKENS


def new_code():
    c = "".join(secrets.choice(ALPHABET) for _ in range(6))
    return c[:3] + "-" + c[3:]


def channel_of(token):
    return "private-screen." + ((TOKENS.get(token) or {}).get("screen") or {}).get("id", "scr_1")


def bell_send(conn, event, data, channel=None):
    msg = {"event": event, "data": json.dumps(data)}
    if channel:
        msg["channel"] = channel
    with conn["lock"]:
        conn["handler"].connection.sendall(encode_frame(json.dumps(msg).encode()))


def ring(event, data=None):
    """Send `event` on every subscribed bel socket (all devices: the mock has one playlist)."""
    with LOCK:
        conns = [c for c in SOCKETS if c["channel"]]
    for c in conns:
        try:
            bell_send(c, event, data or [], c["channel"])
        except OSError:
            pass
    if conns:
        log(f"bel: {event} -> {len(conns)} socket(s)")


def ffprobe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def heartbeat(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if not token_ok(self.headers):
            return self._json(401, {"error": "unauthorized"})
        log(f"heartbeat {self.address_string()}:", body.decode(errors="replace"))
        try:
            agent = json.loads(body or b"{}").get("agent_version")
        except ValueError:
            agent = None
        if STATE["update"] and agent == STATE["update"]["version"]:
            log("update", agent, "done")
            STATE["update"] = None
        self._json(204)

    def bell_socket(self):
        """WebSocket upgrade + the Pusher protocol, as far as a player uses it."""
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            return self._json(400, {"error": "websocket upgrade expected"})
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key(key))
        self.end_headers()
        self.close_connection = True
        conn = {"handler": self, "lock": threading.Lock(), "channel": None,
                "socket_id": f"{secrets.randbelow(10**9)}.{secrets.randbelow(10**9)}"}
        with LOCK:
            SOCKETS.append(conn)
            BELL["connections"] += 1

        def read(n):
            data = self.rfile.read(n)
            if len(data) < n:
                raise EOFError
            return data
        try:
            bell_send(conn, "pusher:connection_established",
                      {"socket_id": conn["socket_id"], "activity_timeout": BELL["activity_timeout"]})
            while True:
                _, opcode, payload = read_frame(read)
                if opcode == 8:
                    break
                if opcode == 9:
                    with conn["lock"]:
                        self.connection.sendall(encode_frame(payload, 10))
                if opcode != 1:
                    continue
                msg = json.loads(payload)
                data = msg.get("data") or {}
                if msg.get("event") == "pusher:ping":
                    with LOCK:
                        BELL["pings"] += 1
                    if BELL["pong"]:
                        bell_send(conn, "pusher:pong", {})
                elif msg.get("event") == "pusher:subscribe":
                    ch = data.get("channel", "")
                    want = BELL_KEY + ":" + hmac.new(BELL_SECRET.encode(), f"{conn['socket_id']}:{ch}".encode(),
                                                     hashlib.sha256).hexdigest()
                    if secrets.compare_digest(str(data.get("auth")), want):
                        conn["channel"] = ch
                        bell_send(conn, "pusher_internal:subscription_succeeded", {}, ch)
                        log(f"bel: {self.address_string()} subscribed to {ch}")
                    else:
                        bell_send(conn, "pusher:error", {"code": 4009, "message": "Connection is unauthorized"})
        except (OSError, EOFError, ValueError):
            pass
        finally:
            with LOCK:
                SOCKETS.remove(conn)

    def bell_auth(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        token = bearer(self.headers)
        ch = body.get("channel_name")
        if not BELL["auth"] or not token_ok(self.headers) or ch != channel_of(token):
            return self._json(403, {"error": "forbidden"})
        sig = hmac.new(BELL_SECRET.encode(), f"{body.get('socket_id')}:{ch}".encode(), hashlib.sha256).hexdigest()
        self._json(200, {"auth": f"{BELL_KEY}:{sig}"})

    def _json(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj, indent=1).encode()
        self.send_response(code)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def pair_page(self, code="", msg=""):
        now = time.time()
        with LOCK:
            open_codes = [c for c, v in CODES.items() if not v["token"] and v["expires_at"] > now]
        pending = "".join(f'<li><a href="/pair?code={c}">{c}</a></li>' for c in open_codes)
        return self._html(200, PAIR_PAGE.format(
            code=html.escape(code), msg=f'<p class="msg">{msg}</p>' if msg else "",
            pending=f"<h2>Waiting screens</h2><ul>{pending}</ul>" if pending else ""))

    def claim(self):
        n = int(self.headers.get("Content-Length") or 0)
        f = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        code = f.get("code", "").strip().upper()
        if len(code) == 6:
            code = code[:3] + "-" + code[3:]
        with LOCK:
            c = CODES.get(code)
            if not c or c["token"] or c["expires_at"] <= time.time():
                bad = True
            else:
                bad = False
                token = "dt_" + secrets.token_hex(12)
                screen = {"id": "scr_" + secrets.token_hex(3), "name": f.get("name") or "Screen",
                          "group_id": f.get("group_id") or "grp_1"}
                c["token"] = token
                TOKENS[token] = {"device_id": c["device_id"], "screen": screen}
        if bad:
            return self.pair_page(code, f"Code {html.escape(code)} is unknown, expired or already used.")
        log(f"claimed {code} for {c['device_id']} as {screen}")
        self.pair_page("", f"Paired {html.escape(c['device_id'])} as <b>{html.escape(screen['name'])}</b>.")

    def register(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            dev = json.loads(self.rfile.read(n) or b"{}")["device_id"]
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "device_id required"})
        code = new_code()
        exp = round(time.time() + ARGS.code_ttl)
        with LOCK:
            for k in [k for k, v in CODES.items() if v["device_id"] == dev and not v["token"]]:
                del CODES[k]  # a new register invalidates the previous unclaimed code
            secret = "ps_" + secrets.token_hex(16)
            CODES[code] = {"device_id": dev, "expires_at": exp, "token": None,
                           "secret": hashlib.sha256(secret.encode()).hexdigest()}
        host = self.headers.get("Host") or f"localhost:{ARGS.port}"
        log(f"register {dev} -> {code}")
        self._json(200, {"code": code, "expires_at": exp, "pair_url": f"http://{host}/pair?code={code}",
                         "poll_interval": 3, "poll_secret": secret})

    def register_status(self, q):
        secret = hashlib.sha256((bearer(self.headers) or "").encode()).hexdigest()
        with LOCK:
            c = CODES.get(q.get("code", ""))
            if not c or c["device_id"] != q.get("device_id") or not secrets.compare_digest(c["secret"], secret):
                return self._json(200, {"status": "expired"})
            if c["token"]:
                del CODES[q["code"]]  # token handed out exactly once
                return self._json(200, {"status": "claimed", "device_token": c["token"],
                                        "screen": TOKENS.get(c["token"], {}).get("screen")})
            if c["expires_at"] <= time.time():
                del CODES[q["code"]]
                return self._json(200, {"status": "expired"})
        self._json(200, {"status": "pending"})

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/api/devices/register/status":
            return self.register_status(q)
        if url.path in ("/", "/pair"):
            return self.pair_page(q.get("code", ""))
        if url.path.startswith("/app/"):
            return self.bell_socket()
        if url.path == "/api/devices/bell":
            if not token_ok(self.headers):
                return self._json(401, {"error": "unauthorized"})
            if not BELL["enabled"]:
                return self._json(204)
            return self._json(200, {"key": BELL_KEY, "host": None, "port": self.server.server_port,
                                    "scheme": "http", "channel": channel_of(bearer(self.headers))})
        if url.path == "/api/devices/manifest":
            if not token_ok(self.headers):
                return self._json(401, {"error": "unauthorized"})
            m = STATE["manifest"]
            screen = TOKENS.get(bearer(self.headers), {}).get("screen")
            until = STATE["identify_until"] if STATE["identify_until"] > time.time() else 0
            if m:
                return self._json(200, {**m, "identify_until": until, "rotation": STATE["rotation"],
                                        "update": STATE["update"], "screen": screen or m["screen"]})
            if until or STATE["update"]:
                return self._json(200, {"version": None, "identify_until": until, "rotation": STATE["rotation"],
                                        "update": STATE["update"],
                                        "screen": screen or {"id": "scr_1", "name": "Mock screen", "group_id": "grp_1"}})
            return self._json(204)
        if url.path.startswith("/media/"):
            name = os.path.basename(urllib.parse.unquote(url.path[len("/media/"):]))
            path = os.path.join(ARGS.media, name)
            if not os.path.isfile(path):
                return self._json(404, {"error": "not found"})
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/api/devices/register":
            return self.register()
        if url.path == "/api/devices/heartbeat":
            return self.heartbeat()
        if url.path == "/api/broadcasting/auth":
            return self.bell_auth()
        if url.path == "/admin/poll":
            ring("poll")
            return self._json(200, {"ok": True})
        if url.path == "/admin/bell":
            for k in ("enabled", "auth", "pong"):
                if k in q:
                    BELL[k] = q[k] == "1"
            if "activity_timeout" in q:
                BELL["activity_timeout"] = float(q["activity_timeout"])
            return self._json(200, {k: BELL[k] for k in ("enabled", "auth", "pong", "activity_timeout")})
        if url.path == "/admin/bell/drop":
            with LOCK:
                conns = list(SOCKETS)
            for c in conns:
                try:
                    c["handler"].connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            log(f"bel: dropped {len(conns)} socket(s)")
            return self._json(200, {"dropped": len(conns)})
        if url.path == "/pair":
            return self.claim()
        if url.path == "/admin/update":
            v = q.get("version")
            STATE["update"] = {"version": v, "url": q.get("url") or
                               f"https://github.com/zekerzichtbaar/zz-narrowcasting-player/archive/refs/tags/{v}.tar.gz"} if v else None
            log("update", json.dumps(STATE["update"]))
            ring("poll")
            return self._json(200, {"update": STATE["update"]})
        if url.path == "/admin/unpublish":
            STATE["manifest"] = None
            ring("poll")
            return self._json(200, {"ok": True})
        if url.path == "/admin/identify":
            STATE["identify_until"] = round(time.time() + float(q.get("seconds", 60)))
            log("identify until", STATE["identify_until"])
            ring("identify", {"identify_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(STATE["identify_until"]))})
            return self._json(200, {"identify_until": STATE["identify_until"]})
        if url.path == "/admin/rotate":
            STATE["rotation"] = int(q.get("rotation", 0))
            log("rotation", STATE["rotation"])
            ring("poll")
            return self._json(200, {"rotation": STATE["rotation"]})
        if url.path == "/admin/activate":
            m = STATE["manifest"]
            if not m:
                return self._json(404, {"error": "publish first"})
            at = round(time.time() + float(q.get("delay", 45)), 3)
            STATE["manifest"] = {**m, "version": f"{m['sha256'][:8]}-{at}", "activate_at": at, "epoch": at}
            log("group moment", json.dumps(STATE["manifest"]))
            ring("poll")
            return self._json(200, STATE["manifest"])
        if url.path == "/admin/unpair":
            dev = q.get("device_id")
            with LOCK:
                gone = [t for t, v in TOKENS.items() if dev is None or v["device_id"] == dev]
                for t in gone:
                    del TOKENS[t]
            log(f"unpaired: revoked {len(gone)} token(s)")
            ring("poll")
            return self._json(200, {"ok": True, "revoked": len(gone)})
        if url.path == "/admin/pair":
            token = q.get("token") or ARGS.token
            TOKENS[token] = {"device_id": None, "screen": None}
            return self._json(200, {"ok": True, "token": token})
        if url.path != "/admin/publish" or "file" not in q:
            return self._json(404, {"error": "POST /admin/publish?file=NAME&delay=SECONDS"})
        name = os.path.basename(q["file"])
        path = os.path.join(ARGS.media, name)
        if not os.path.isfile(path):
            return self._json(404, {"error": f"{name} not in {ARGS.media}"})
        digest = sha256(path)
        if q.get("bad_sha"):
            digest = "0" * 64  # lets you test that the Pi rejects a corrupt download
        now = time.time()
        wait = bool(q.get("wait"))
        STATE["manifest"] = {
            "version": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)) + "-" + digest[:4] + ("-wait" if wait else ""),
            "url": "/media/" + urllib.parse.quote(name),
            "sha256": digest,
            "size": os.path.getsize(path),
            "duration": float(q["duration"]) if "duration" in q else ffprobe_duration(path),
            "epoch": 0.0 if wait else float(q.get("epoch", 0)),
            "activate_at": 4102444800 if wait else round(now + float(q.get("delay", 60)), 3),
            "orientation": q.get("orientation", "landscape"),
            "screen": {"id": "scr_1", "name": "Mock screen", "group_id": "grp_1"},
        }
        log("published", json.dumps(STATE["manifest"]))
        ring("poll")
        self._json(200, STATE["manifest"])

    def log_message(self, fmt, *a):
        log(self.address_string(), fmt % a)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--token", default="devtoken", help="the one valid device token")
    p.add_argument("--media", default=".", help="directory holding the MP4s to publish")
    p.add_argument("--code-ttl", type=float, default=600, help="pairing code lifetime (s)")
    return p.parse_args(argv)


def make_server(args):
    global ARGS
    ARGS = args
    TOKENS[ARGS.token] = {"device_id": None, "screen": None}
    ARGS.media = os.path.abspath(ARGS.media)
    srv = http.server.ThreadingHTTPServer((ARGS.bind, ARGS.port), Handler)
    ARGS.port = srv.server_port
    return srv


def main():
    srv = make_server(parse_args())
    print(f"mock CMS on http://{ARGS.bind}:{ARGS.port}  media={ARGS.media}  token={ARGS.token}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
