"""Captive portal served on the setup hotspot (stdlib http.server, runs inside the agent).

Every unknown host/path (the iOS/Android/Windows captive-portal probes included, DNS is
wildcarded to us) gets a redirect to the setup page, so phones open it by themselves.

Self-check:                           python3 -m pi_player.portal
Dev preview with a fake network list: python3 -m pi_player.portal serve [PORT]
"""
import html
import http.server
import logging
import os
import threading
import urllib.parse

log = logging.getLogger("pi-player")

# Same look as the TV screens (screens.py palette + fonts); fonts and logo are served by us,
# the hotspot has no internet.
CSS = """
@font-face{font-family:Bricolage;src:url(/a/display.ttf);font-display:swap}
@font-face{font-family:GeistMono;src:url(/a/mono.ttf);font-display:swap}
*{box-sizing:border-box}html{background:#07080a}
body{margin:0;min-height:100vh;font:17px/1.45 system-ui,-apple-system,sans-serif;color:#f2f4f6;
background:radial-gradient(circle at 10% 0,rgba(61,220,132,.22),transparent 340px),
radial-gradient(circle at 85% 100%,rgba(37,70,240,.22),transparent 380px) no-repeat}
main{max-width:480px;margin:0 auto;padding:20px 16px 32px}
.top{display:flex;align-items:center;gap:10px;margin:0 0 28px}
.top b{font:20px Bricolage,system-ui,sans-serif;flex:1}.top svg{width:36px;height:36px}
.mono{font-family:GeistMono,ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em}
.top small{font-size:11px;color:#a3abb7;display:flex;align-items:center;gap:6px}
.top small:before{content:"";width:8px;height:8px;border-radius:50%;background:#f5b83d}
h1{font:30px/1.15 Bricolage,system-ui,sans-serif;margin:0 0 8px}p{color:#a3abb7;margin:0 0 20px}
.box{border-radius:12px;padding:12px 14px;margin:0 0 20px;border:1px solid}
.err{background:rgba(255,110,100,.12);border-color:rgba(255,110,100,.4);color:#ff6e64}
.ok{background:rgba(61,220,132,.1);border-color:rgba(61,220,132,.35);color:#c9d1db}.ok b{color:#3ddc84}
h2{font-size:16px;margin:26px 0 10px;color:#c9d1db;font-weight:500;display:flex;gap:12px;align-items:center}
h2 i{font:normal 13px GeistMono,ui-monospace,monospace;display:inline-grid;place-items:center;
width:28px;height:28px;border-radius:50%;border:2px solid #3a414c;color:#f2f4f6}
label.net{display:flex;align-items:center;gap:12px;padding:14px;border:2px solid #3a414c;
border-radius:12px;margin:0 0 8px;cursor:pointer;min-height:56px;background:rgba(7,8,10,.6)}
label.net:has(input:checked){border-color:#3ddc84;background:rgba(61,220,132,.08)}
label.net input{width:20px;height:20px;accent-color:#3ddc84;margin:0}
label.net span{flex:1;overflow-wrap:anywhere;font-weight:500}label.net small{color:#a3abb7;white-space:nowrap}
details{margin:4px 0 0;color:#a3abb7}summary{cursor:pointer;padding:8px 0}
input[type=text],input[type=password]{width:100%;font:inherit;padding:14px;border-radius:12px;
border:2px solid #3a414c;background:rgba(7,8,10,.6);color:inherit;margin:4px 0 8px}
input:focus{outline:none;border-color:#3ddc84}::placeholder{color:#6b7380}
.show{display:flex;gap:8px;align-items:center;color:#a3abb7;font-size:15px;margin:0 0 16px}
.show input{accent-color:#3ddc84}
button{font:inherit;font-weight:700;padding:16px;border-radius:12px;border:0;width:100%;
background:#3ddc84;color:#04200e;margin:8px 0 0}
button.sec{background:none;color:#a3abb7;font-weight:500;padding:10px;text-decoration:underline}
.foot{font-size:14px;margin:28px 0 0;text-align:center;color:#6b7380}
.zz{display:flex;justify-content:center;align-items:center;gap:8px;margin:20px 0 0;
font:14px Bricolage,system-ui,sans-serif;color:#c8cacc}.zz img{height:21px}
"""

# The Narrowcasting mark (screens._mark / CMS app-logo-icon.tsx)
MARK = """<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="7.8" fill="#3ddc84"/>
<g transform="translate(8 8) scale(.75) translate(-4 -4.5)" fill="none" stroke="#04200e" stroke-width="3"
stroke-linecap="round"><rect x="6" y="9" width="28" height="18.5" rx="3.5"/><path d="M14.5 32.5h11"/>
<path d="M17.5 14.25v8l6.5-4z" fill="#04200e" stroke="none"/><circle cx="33" cy="10" r="5.75"
fill="#3ddc84" stroke="none"/><circle cx="33" cy="10" r="4.5" fill="#ff0080" stroke="none"/></g></svg>"""

_DIR = os.path.dirname(__file__)
ASSETS = {"/a/display.ttf": ("fonts/BricolageGrotesque-Bold.ttf", "font/ttf"),
          "/a/mono.ttf": ("fonts/GeistMono-Medium.ttf", "font/ttf"),
          "/a/zz.png": ("zz-logo.png", "image/png")}


def _bars(signal):
    return "▂▄▆█"[: max(1, min(4, (signal + 24) // 25))]


def page(ssid, networks, error=None, notice=None, status=None):
    """The setup page (pure; also used by the self-check)."""
    e = html.escape
    rows = []
    for i, n in enumerate(networks):
        lock = "🔒 " if n["security"] else ""
        rows.append(
            f'<label class="net"><input type="radio" name="ssid" value="{e(n["ssid"])}"'
            f'{" checked" if i == 0 else ""}><span>{e(n["ssid"])}</span>'
            f'<small>{lock}{_bars(n["signal"])}</small></label>')
    nets = "".join(rows) or "<p>Geen netwerken gevonden. Vernieuw de lijst, of typ de naam hieronder.</p>"
    msg = ""
    if error:
        msg += f'<div class="box err">{e(error)}</div>'
    if notice:
        msg += f'<div class="box ok">{notice}</div>'
    if status:
        msg += f'<div class="box ok">{e(status)}</div>'
    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scherm instellen</title>
<style>{CSS}</style></head><body><main>
<div class="top">{MARK}<b>Narrowcasting</b><small class="mono">Geen verbinding</small></div>
<h1>Verbind het scherm met wifi</h1><p>Kies het netwerk dat het scherm moet gebruiken.</p>
{msg}
<form method="post" action="/connect">
<h2><i>1</i>Kies het netwerk</h2>{nets}
<details{"" if networks else " open"}><summary>Staat je netwerk er niet bij?</summary>
<input type="text" name="other" placeholder="Netwerknaam (verborgen netwerk)" autocapitalize="off"
autocorrect="off"></details>
<h2><i>2</i>Wachtwoord van dat netwerk</h2>
<input type="password" id="pw" name="password" placeholder="Leeg laten bij een open netwerk"
autocomplete="off" autocapitalize="off" autocorrect="off">
<label class="show"><input type="checkbox" onchange="pw.type=this.checked?'text':'password'">
Wachtwoord tonen</label>
<button type="submit">Verbinden</button></form>
<form method="post" action="/rescan"><button class="sec" type="submit">Lijst vernieuwen</button></form>
<p class="foot">Je bent verbonden met het instelnetwerk {e(ssid or "")} van het scherm.</p>
<div class="zz"><img src="/a/zz.png" alt="">Zeker Zichtbaar</div>
</main></body></html>"""


def validate(form):
    """form (parse_qs dict) -> (ssid, password, None) or (None, None, error)."""
    ssid = (form.get("other", [""])[0].strip() or form.get("ssid", [""])[0])
    pw = form.get("password", [""])[0]
    if not ssid:
        return None, None, "Kies een netwerk of typ de naam."
    if len(ssid.encode()) > 32:
        return None, None, "Netwerknaam is te lang."
    if pw and not 8 <= len(pw) <= 63:
        return None, None, "Een wifi-wachtwoord is 8 tot 63 tekens."
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
        path = urllib.parse.urlsplit(self.path).path
        if self._ours() and path in ASSETS:
            f, ctype = ASSETS[path]
            with open(os.path.join(_DIR, f), "rb") as fh:
                return self._send(200, fh.read(), [("Content-Type", ctype)])
        if not self._ours() or path != "/":
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
                f"Netwerken zoeken… het instelnetwerk <b>{html.escape(c.ssid)}</b> is ongeveer 20 "
                "seconden weg. Maak daarna opnieuw verbinding, dan komt deze pagina terug met een "
                "nieuwe lijst."))
        if path != "/connect":
            return self._redirect()
        ssid, pw, err = validate(form)
        if err:
            return self._page(error=err)
        c.connect(ssid, pw)
        self._page(notice=(
            f"Het scherm verbindt nu met <b>{html.escape(ssid)}</b>. Het instelnetwerk gaat uit, je "
            "kunt deze pagina sluiten. Lukt het niet, dan komt het instelnetwerk binnen een minuut "
            "terug met een melding op het scherm; maak opnieuw verbinding en probeer het nog eens."))


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
        self.error = f"Verbinden met {ssid} mislukt: Verkeerd wachtwoord?"  # so the reload shows an error

    def rescan(self):
        print("rescan")


def _selfcheck():
    import urllib.request
    p = page("Narrowcast-1E2F", _FakeCtrl.networks, error="Bad <pw>")
    assert "Office &lt;5G&gt;" in p and "Bad &lt;pw&gt;" in p and p.count('type="radio"') == 3
    assert "Geen netwerken gevonden" in page("x", [])
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
    assert urllib.request.urlopen(f"http://127.0.0.1:{port}/a/mono.ttf").read()[:4] == b"\0\1\0\0"
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}/connect",
                               data=b"ssid=Kantine&password=short").read().decode()
    assert "8 tot 63" in r
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
