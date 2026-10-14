"""Captive portal served on the setup hotspot (stdlib http.server, runs inside the agent).

Every unknown host/path (the iOS/Android/Windows captive-portal probes included, DNS is
wildcarded to us) gets a redirect to the setup page, so phones open it by themselves.

Self-check:                           python3 -m pi_player.portal
Dev preview with a fake network list: python3 -m pi_player.portal serve [PORT]
"""
import html
import http.server
import logging
import threading
import urllib.parse

log = logging.getLogger("pi-player")

CSS = """
*{box-sizing:border-box}body{margin:0;font:16px/1.45 system-ui,-apple-system,sans-serif;
background:#121418;color:#f0f0f0}main{max-width:480px;margin:0 auto;padding:24px 16px 40px}
h1{font-size:24px;margin:0 0 4px}p{color:#969ba5;margin:4px 0 16px}
.box{border-radius:10px;padding:12px 14px;margin:0 0 16px}.err{background:#4a1d1d;color:#ffd0d0}
.ok{background:#1d3a26;color:#d4f5dc}label.net{display:flex;align-items:center;gap:10px;
padding:12px;border:1px solid #2c3038;border-radius:10px;margin:0 0 8px;cursor:pointer}
label.net span{flex:1;overflow-wrap:anywhere}small{color:#969ba5}
input[type=text],input[type=password]{width:100%;font:inherit;padding:12px;border-radius:10px;
border:1px solid #2c3038;background:#1b1e24;color:inherit;margin:4px 0 12px}
button{font:inherit;font-weight:600;padding:12px 16px;border-radius:10px;border:0;width:100%;
background:#ffc400;color:#121418;margin:4px 0}button.sec{background:#2c3038;color:#f0f0f0}
h2{font-size:16px;margin:20px 0 8px;color:#969ba5;font-weight:600}
"""


def _bars(signal):
    return "▂▄▆█"[: max(1, min(4, (signal + 24) // 25))]


def page(ssid, networks, error=None, notice=None, status=None):
    """The setup page (pure; also used by the self-check)."""
    e = html.escape
    rows = []
    for i, n in enumerate(networks):
        lock = " <small>(secured)</small>" if n["security"] else ""
        rows.append(
            f'<label class="net"><input type="radio" name="ssid" value="{e(n["ssid"])}"'
            f'{" checked" if i == 0 else ""}><span>{e(n["ssid"])}{lock}</span>'
            f'<small>{_bars(n["signal"])}</small></label>')
    nets = "".join(rows) or "<p>No networks found. Try a rescan, or type the name below.</p>"
    msg = ""
    if error:
        msg += f'<div class="box err">{e(error)}</div>'
    if notice:
        msg += f'<div class="box ok">{notice}</div>'
    if status:
        msg += f'<div class="box ok">{e(status)}</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Screen wifi setup</title>
<style>{CSS}</style></head><body><main>
<h1>Connect this screen to wifi</h1><p>Pick the network the screen should use and enter its password.</p>
{msg}
<form method="post" action="/connect">
<h2>Networks in range</h2>{nets}
<h2>Not listed? (hidden network)</h2>
<input type="text" name="other" placeholder="Network name" autocapitalize="off" autocorrect="off">
<h2>Password</h2>
<input type="password" name="password" placeholder="Leave empty for an open network" autocomplete="off">
<button type="submit">Connect</button></form>
<form method="post" action="/rescan"><button class="sec" type="submit">Rescan networks</button></form>
<p><small>You are connected to the screen's setup network {e(ssid or "")}.</small></p>
</main></body></html>"""


def validate(form):
    """form (parse_qs dict) -> (ssid, password, None) or (None, None, error)."""
    ssid = (form.get("other", [""])[0].strip() or form.get("ssid", [""])[0])
    pw = form.get("password", [""])[0]
    if not ssid:
        return None, None, "Pick a network or type its name."
    if len(ssid.encode()) > 32:
        return None, None, "Network name is too long."
    if pw and not 8 <= len(pw) <= 63:
        return None, None, "Wifi passwords are 8 to 63 characters."
    return ssid, pw, None


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "pi-player"

    def log_message(self, fmt, *args):
        log.debug("portal: " + fmt, *args)

    def _ours(self):
        return (self.headers.get("Host") or "").lower() in self.server.hosts

    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self):
        self._send(302, b"", [("Location", self.server.base + "/")])

    def _page(self, **kw):
        c = self.server.ctrl
        self._send(200, page(c.ssid, c.networks, **kw).encode(),
                   [("Content-Type", "text/html; charset=utf-8")])

    def do_GET(self):
        self.server.ctrl.touch()
        if not self._ours() or urllib.parse.urlsplit(self.path).path != "/":
            return self._redirect()  # captive-portal probes land here
        c = self.server.ctrl
        self._page(error=c.error, status=c.status)

    do_HEAD = do_GET

    def do_POST(self):
        c = self.server.ctrl
        c.touch()
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        path = urllib.parse.urlsplit(self.path).path
        if path == "/rescan":
            c.rescan()
            return self._page(notice=(
                f"Rescanning… the setup network <b>{html.escape(c.ssid)}</b> disappears for about "
                "20 seconds. Rejoin it afterwards and this page comes back with a fresh list."))
        if path != "/connect":
            return self._redirect()
        ssid, pw, err = validate(form)
        if err:
            return self._page(error=err)
        c.connect(ssid, pw)
        self._page(notice=(
            f"Connecting the screen to <b>{html.escape(ssid)}</b>. The setup network turns off now "
            "— you can close this page. If it fails, the setup network comes back within a minute "
            "with an error on the screen; rejoin it and try again."))


class Portal:
    def __init__(self, ctrl, host, port=80):
        self.httpd = http.server.ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.ctrl = ctrl
        hostport = host if port == 80 else f"{host}:{port}"
        self.httpd.base = "http://" + hostport
        self.httpd.hosts = {host, hostport}

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, name="portal", daemon=True).start()
        log.info("portal on %s", self.httpd.base)
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _FakeCtrl:
    ssid, error, status = "Narrowcast-1E2F", None, None
    networks = [{"ssid": "Kantine", "signal": 82, "security": "WPA2"},
                {"ssid": "Office <5G>", "signal": 54, "security": "WPA2 WPA3"},
                {"ssid": "Guest", "signal": 20, "security": ""}]

    def touch(self):
        pass

    def connect(self, ssid, pw):
        print(f"connect {ssid!r} pw={len(pw)} chars")
        self.error = f"Could not connect to {ssid}: Wrong password?"  # so the reload shows an error

    def rescan(self):
        print("rescan")


def _selfcheck():
    import urllib.request
    p = page("Narrowcast-1E2F", _FakeCtrl.networks, error="Bad <pw>")
    assert "Office &lt;5G&gt;" in p and "Bad &lt;pw&gt;" in p and p.count('type="radio"') == 3
    assert "No networks found" in page("x", [])
    assert validate({"ssid": ["Kantine"], "password": ["secret123"]}) == ("Kantine", "secret123", None)
    assert validate({"ssid": ["Kantine"], "other": [" Hidden "], "password": [""]}) == ("Hidden", "", None)
    assert validate({"password": ["secret123"]})[2]
    assert validate({"ssid": ["K"], "password": ["short"]})[2]
    assert validate({"ssid": ["x" * 33]})[2]
    srv = Portal(_FakeCtrl(), "127.0.0.1", 0)
    port = srv.httpd.server_address[1]
    srv.httpd.base = f"http://127.0.0.1:{port}"
    srv.httpd.hosts = {f"127.0.0.1:{port}"}
    srv.start()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/generate_204",
                                 headers={"Host": "connectivitycheck.gstatic.com"})
    try:
        op.open(req)
        raise AssertionError("probe was not redirected")
    except urllib.error.HTTPError as e:
        assert e.code == 302 and e.headers["Location"] == f"http://127.0.0.1:{port}/"
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
    assert "Kantine" in body
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}/connect",
                               data=b"ssid=Kantine&password=short").read().decode()
    assert "8 to 63" in r
    srv.stop()
    print("portal self-check OK")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        _selfcheck()
        sys.exit(0)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8099
    logging.basicConfig(level=logging.DEBUG)
    print(f"portal preview on http://127.0.0.1:{port}/")
    Portal(_FakeCtrl(), "127.0.0.1", port).httpd.serve_forever()
