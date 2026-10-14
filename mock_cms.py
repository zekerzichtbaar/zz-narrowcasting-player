#!/usr/bin/env python3
"""Tiny mock CMS for local testing (see docs/protocol.md). Stdlib only.

  python3 mock_cms.py [--port 8080] [--token devtoken] [--media DIR] [--code-ttl 600]

Device endpoints:
  POST /api/devices/register     -> pairing code + poll_secret (invalidates the device's previous code)
  GET  /api/devices/register/status?device_id=&code=   bearer poll_secret; pending / expired /
                                 claimed (+token, once); a wrong or missing secret = expired
  GET  /api/devices/manifest     bearer token; 200 manifest / 204 nothing published / 401
  POST /api/devices/heartbeat    bearer token; logs it, 204 / 401
  GET  /media/<file>             the MP4s in --media
Admin (browser): GET /pair[?code=K7Q-4M2]  enter code + screen name/group -> device gets a token
Admin (curl):
  POST /admin/publish?file=a.mp4&delay=60[&duration=10][&epoch=0][&bad_sha=1][&wait=1]
                                 wait=1: "waiting for the group" (activate_at 4102444800, epoch 0)
  POST /admin/activate?delay=45  group moment: same file, new version, activate_at = epoch = now+delay
  POST /admin/unpublish          back to 204
  POST /admin/identify[?seconds=60]   "Scherm identificeren": identify_until = now+seconds in every
                                 manifest (version unchanged; nothing published = 200 version null)
  POST /admin/unpair[?device_id=pi-...]   revoke tokens (all, or that device's): next call gets 401
  POST /admin/pair               re-enable the hand-provisioned --token (for testing without pairing)
Devices pick changes up at their next manifest poll (default every 30 s).
"""
import argparse
import hashlib
import html
import http.server
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse

STATE = {"manifest": None, "identify_until": 0}
TOKENS = {}  # device token -> {"device_id", "screen"}; the --token one is added in main()
CODES = {}   # pairing code -> {"device_id", "expires_at", "token"|None}
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
        self._json(204)

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
        if url.path == "/api/devices/manifest":
            if not token_ok(self.headers):
                return self._json(401, {"error": "unauthorized"})
            m = STATE["manifest"]
            screen = TOKENS.get(bearer(self.headers), {}).get("screen")
            until = STATE["identify_until"] if STATE["identify_until"] > time.time() else 0
            if m:
                return self._json(200, {**m, "identify_until": until, "screen": screen or m["screen"]})
            if until:
                return self._json(200, {"version": None, "identify_until": until,
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
        if url.path == "/pair":
            return self.claim()
        if url.path == "/admin/unpublish":
            STATE["manifest"] = None
            return self._json(200, {"ok": True})
        if url.path == "/admin/identify":
            STATE["identify_until"] = round(time.time() + float(q.get("seconds", 60)))
            log("identify until", STATE["identify_until"])
            return self._json(200, {"identify_until": STATE["identify_until"]})
        if url.path == "/admin/activate":
            m = STATE["manifest"]
            if not m:
                return self._json(404, {"error": "publish first"})
            at = round(time.time() + float(q.get("delay", 45)), 3)
            STATE["manifest"] = {**m, "version": f"{m['sha256'][:8]}-{at}", "activate_at": at, "epoch": at}
            log("group moment", json.dumps(STATE["manifest"]))
            return self._json(200, STATE["manifest"])
        if url.path == "/admin/unpair":
            dev = q.get("device_id")
            with LOCK:
                gone = [t for t, v in TOKENS.items() if dev is None or v["device_id"] == dev]
                for t in gone:
                    del TOKENS[t]
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
        wait = bool(q.get("wait"))
        STATE["manifest"] = {
            "version": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)) + "-" + digest[:4] + ("-wait" if wait else ""),
            "url": "/media/" + urllib.parse.quote(name),
            "sha256": digest,
            "size": os.path.getsize(path),
            "duration": float(q["duration"]) if "duration" in q else ffprobe_duration(path),
            "epoch": 0.0 if wait else float(q.get("epoch", 0)),
            "activate_at": 4102444800 if wait else round(now + float(q.get("delay", 60)), 3),
            "screen": {"id": "scr_1", "name": "Mock screen", "group_id": "grp_1"},
        }
        log("published", json.dumps(STATE["manifest"]))
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
