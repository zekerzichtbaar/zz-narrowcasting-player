#!/usr/bin/env python3
"""Tiny mock CMS for local testing (see docs/protocol.md). Stdlib only.

  python3 mock_cms.py [--port 8080] [--token devtoken] [--media DIR] [--code-ttl 600]

Device endpoints:
  POST /api/devices/register     -> pairing code (invalidates the device's previous code)
  GET  /api/devices/register/status?device_id=&code=   pending / expired / claimed (+token, once)
  GET  /api/devices/manifest     bearer token; 200 manifest / 204 nothing published / 401
  GET  /media/<file>             the MP4s in --media
  GET  /api/devices/ws           WebSocket (same port, hand-rolled RFC 6455); logs every message
Admin (browser): GET /pair[?code=K7Q-4M2]  enter code + screen name/group -> device gets a token
Admin (curl):
  POST /admin/publish?file=a.mp4&delay=60[&duration=10][&epoch=0][&bad_sha=1]
                                 also pushes update_available to connected devices
  POST /admin/unpublish          back to 204 (+update_available so devices refetch)
  POST /admin/unpair[?device_id=pi-...]   revoke tokens (all, or that device's), push unpaired + close 4401
  POST /admin/pair               re-enable the hand-provisioned --token (for testing without pairing)
"""
import argparse
import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import shutil
import struct
import subprocess
import threading
import time
import urllib.parse

STATE = {"manifest": None}
TOKENS = {}  # device token -> {"device_id", "screen"}; the --token one is added in main()
CODES = {}   # pairing code -> {"device_id", "expires_at", "token"|None}
CLIENTS = set()  # connected WS handlers (each has .token)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
LOCK = threading.Lock()

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


def broadcast(msg):
    for c in list(CLIENTS):
        c.ws_send(json.dumps(msg))
    log(f"pushed to {len(CLIENTS)} device(s):", json.dumps(msg))


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

    # ---- minimal RFC 6455 server side: text frames, ping/pong, close ----
    def ws_send(self, data, opcode=1):
        payload = data.encode() if isinstance(data, str) else data
        n = len(payload)
        head = struct.pack("!BB", 0x80 | opcode, n) if n < 126 else (
            struct.pack("!BBH", 0x80 | opcode, 126, n) if n < 65536 else struct.pack("!BBQ", 0x80 | opcode, 127, n))
        with self.ws_lock:
            try:
                self.wfile.write(head + payload)
                self.wfile.flush()
            except OSError:
                pass

    def ws_close(self, code, reason=""):
        self.ws_send(struct.pack("!H", code) + reason.encode(), opcode=8)

    def ws_read(self):
        b1, b2 = self.rfile.read(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self.rfile.read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self.rfile.read(8))[0]
        mask = self.rfile.read(4) if b2 & 0x80 else b"\0\0\0\0"
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(self.rfile.read(n)))
        return b1 & 0x0F, data

    def serve_ws(self):
        if self.headers.get("Upgrade", "").lower() != "websocket":
            return self._json(400, {"error": "websocket upgrade expected"})
        if not token_ok(self.headers):
            return self._json(401, {"error": "unauthorized"})
        accept = base64.b64encode(hashlib.sha1(
            (self.headers["Sec-WebSocket-Key"] + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        self.ws_lock = threading.Lock()
        self.token = bearer(self.headers)
        CLIENTS.add(self)
        log(f"ws {self.address_string()} connected ({len(CLIENTS)} online)")
        try:
            while True:
                op, data = self.ws_read()
                if op == 8:  # close: echo and stop
                    self.ws_send(data[:2], opcode=8)
                    break
                if op == 9:
                    self.ws_send(data, opcode=10)
                elif op == 1:
                    log(f"ws {self.address_string()} <-", data.decode(errors="replace"))
        except (OSError, ValueError):
            pass
        finally:
            CLIENTS.discard(self)
            self.close_connection = True
            log(f"ws {self.address_string()} disconnected ({len(CLIENTS)} online)")

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
            CODES[code] = {"device_id": dev, "expires_at": exp, "token": None}
        host = self.headers.get("Host") or f"localhost:{ARGS.port}"
        log(f"register {dev} -> {code}")
        self._json(200, {"code": code, "expires_at": exp, "pair_url": f"http://{host}/pair?code={code}",
                         "poll_interval": 3})

    def register_status(self, q):
        with LOCK:
            c = CODES.get(q.get("code", ""))
            if not c or c["device_id"] != q.get("device_id"):
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
        if url.path == "/api/devices/ws":
            return self.serve_ws()
        if url.path == "/api/devices/register/status":
            return self.register_status(q)
        if url.path in ("/", "/pair"):
            return self.pair_page(q.get("code", ""))
        if url.path == "/api/devices/manifest":
            if not token_ok(self.headers):
                return self._json(401, {"error": "unauthorized"})
            m = STATE["manifest"]
            screen = TOKENS.get(bearer(self.headers), {}).get("screen")
            return self._json(200, {**m, "screen": screen or m["screen"]}) if m else self._json(204)
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
        if url.path == "/pair":
            return self.claim()
        if url.path == "/admin/unpublish":
            STATE["manifest"] = None
            broadcast({"type": "update_available", "version": None})
            return self._json(200, {"ok": True})
        if url.path == "/admin/unpair":
            dev = q.get("device_id")
            with LOCK:
                gone = [t for t, v in TOKENS.items() if dev is None or v["device_id"] == dev]
                for t in gone:
                    del TOKENS[t]
            for c in [c for c in list(CLIENTS) if c.token in gone]:
                c.ws_send(json.dumps({"type": "unpaired"}))
                c.ws_close(4401, "unpaired")
            log(f"unpaired: revoked {len(gone)} token(s)")
            return self._json(200, {"ok": True, "revoked": len(gone)})
        if url.path == "/admin/pair":
            TOKENS[ARGS.token] = {"device_id": None, "screen": None}
            return self._json(200, {"ok": True, "token": ARGS.token})
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
        STATE["manifest"] = {
            "version": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)) + "-" + digest[:4],
            "url": "/media/" + urllib.parse.quote(name),
            "sha256": digest,
            "size": os.path.getsize(path),
            "duration": float(q["duration"]) if "duration" in q else ffprobe_duration(path),
            "epoch": float(q.get("epoch", 0)),
            "activate_at": round(now + float(q.get("delay", 60)), 3),
            "screen": {"id": "scr_1", "name": "Mock screen", "group_id": "grp_1"},
        }
        log("published", json.dumps(STATE["manifest"]))
        broadcast({"type": "update_available", "version": STATE["manifest"]["version"]})
        self._json(200, STATE["manifest"])

    def log_message(self, fmt, *a):
        log(self.address_string(), fmt % a)


def main():
    global ARGS
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--token", default="devtoken", help="the one valid device token")
    p.add_argument("--media", default=".", help="directory holding the MP4s to publish")
    p.add_argument("--code-ttl", type=float, default=600, help="pairing code lifetime (s)")
    ARGS = p.parse_args()
    TOKENS[ARGS.token] = {"device_id": None, "screen": None}
    ARGS.media = os.path.abspath(ARGS.media)
    srv = http.server.ThreadingHTTPServer((ARGS.bind, ARGS.port), Handler)
    print(f"mock CMS on http://{ARGS.bind}:{ARGS.port}  media={ARGS.media}  token={ARGS.token}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
