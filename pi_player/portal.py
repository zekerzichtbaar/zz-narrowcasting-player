"""Captive portal served on the setup hotspot (stdlib http.server, runs inside the agent).

Every unknown host/path (the iOS/Android/Windows captive-portal probes included, DNS is
wildcarded to us) gets a redirect to the setup page, so phones open it by themselves. DHCP
option 114 (install.sh) points at "/" too; a client that asks it for `application/captive+json`
(RFC 8908) gets the small API answer instead of the page.

Self-check:                           python3 -m pi_player.portal
Dev preview with a fake network list: python3 -m pi_player.portal serve [PORT]
"""
import html
import http.server
import json
import logging
import os
import threading
import urllib.parse

log = logging.getLogger("pi-player")

# Same look as the TV screens (screens.py, huisstijl D3): ink, a faint magenta glow, GT Walsheim for
# text, Impact for the heading, Geist Mono for data. Fonts are served by us: the hotspot has no internet.
CSS = """
@font-face{font-family:Walsheim;src:url(/a/sans.ttf);font-display:swap}
@font-face{font-family:Walsheim;src:url(/a/bold.ttf);font-weight:700;font-display:swap}
@font-face{font-family:ZZImpact;src:url(/a/impact.ttf);font-display:swap}
@font-face{font-family:GeistMono;src:url(/a/mono.ttf);font-display:swap}
*{box-sizing:border-box}html{background:#0c0c11;color-scheme:dark}
body{margin:0;min-height:100vh;font:17px/1.45 Walsheim,system-ui,-apple-system,sans-serif;color:#fff;
background:radial-gradient(ellipse at 0 0,rgba(255,0,128,.16),transparent 60%) no-repeat;accent-color:#ff0080;
caret-color:#ff0080}
::selection{background:#ff0080;color:#fff}
main{max-width:480px;margin:0 auto;padding:20px 16px 32px}
.top{display:flex;align-items:center;gap:10px;margin:0 0 32px}
.top svg{width:44px;height:36px;flex:none}
.top b{flex:1;font-size:15px;font-weight:400;letter-spacing:.14em;text-transform:uppercase;white-space:nowrap}
.top b strong{font-weight:700}
.mono{font-family:GeistMono,ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em}
.top small{font-size:11px;color:#ffc24d;background:rgba(255,194,77,.15);border-radius:14px;padding:5px 10px;
display:flex;align-items:center;gap:6px;white-space:nowrap}
.top small:before{content:"";width:7px;height:7px;border-radius:50%;background:#ffc24d}
h1{font:40px/1.05 ZZImpact,Impact,'Arial Narrow',sans-serif;text-transform:uppercase;margin:0 0 10px;
text-wrap:balance}
p{color:#a9a9b6;margin:0 0 20px}
.box{border-radius:14px;padding:12px 14px;margin:0 0 20px;border:1px solid}
.err{background:rgba(255,122,102,.12);border-color:rgba(255,122,102,.4);color:#ff7a66}
.ok{background:rgba(74,222,155,.1);border-color:rgba(74,222,155,.35);color:#d6d6de}.ok b{color:#4ade9b}
h2{font-size:16px;margin:28px 0 10px;color:#a9a9b6;font-weight:400;display:flex;gap:12px;align-items:center}
h2 i{font:normal 13px GeistMono,ui-monospace,monospace;color:#ff0080}
label.net{display:flex;align-items:center;gap:12px;padding:14px;border:1px solid #2a2a34;
border-radius:8px;margin:0 0 8px;cursor:pointer;min-height:56px;background:#16161d}
label.net:has(input:checked){border-color:#fff}
label.net input{width:20px;height:20px;margin:0}
label.net span{flex:1;overflow-wrap:anywhere}label.net small{color:#a9a9b6;white-space:nowrap}
details{margin:4px 0 0;color:#a9a9b6}summary{cursor:pointer;padding:8px 0}
input[type=text],input[type=password]{width:100%;font:inherit;padding:13px 14px;border-radius:8px;
border:1px solid #2a2a34;background:#16161d;color:inherit;margin:4px 0 8px}
input:focus{outline:none;border-color:#fff}::placeholder{color:#8a8a98}
.show{display:flex;gap:8px;align-items:center;color:#a9a9b6;font-size:15px;margin:0 0 16px}
button{font:inherit;font-weight:700;padding:16px;border-radius:8px;border:0;width:100%;
background:#fff;color:#0c0c11;margin:8px 0 0}
button.sec{background:none;color:#a9a9b6;font-weight:400;padding:10px;text-decoration:underline;
text-underline-offset:3px}
.foot{font-size:14px;margin:28px 0 0;text-align:center;color:#8a8a98}
"""

# Logo D3 (screens._mark / CMS app-logo-icon.tsx): magenta blob, white z, white TV sticker with ink.
MARK = """<svg viewBox="0 0 60 48" aria-hidden="true">
<path fill="#ff0080" d="m49.828 9.4869c-6.4792-5.493-15.954-10.244-27.799-9.333-1.3267 0.1021-2.6662 0.25462-4.0154 0.4632-20.703 3.197-22.511 28.814-10.768 40.89 6.4852 6.6703 17.102 9.2108 30.955 1.5644 4.1535-2.2928 7.5544-4.6361 10.281-6.9866 12.181-10.5 9.5797-19.617 1.3457-26.598z"/>
<path fill="#fff" d="m36.892 26.466c-2.2008-1.5402-6.0661-0.072-8.4833 0.2687 3.0896-2.9715 11.332-6.2127 8.0968-9.9872-3.8171-4.6348-14.86-3.3398-17.177-0.5449-0.8701 1.0491-1.0906 2.999 0.2728 3.8168 2.7266 1.9085 8.8074-0.2728 8.8074-0.2728-3.5447 2.9994-11.261 5.7257-9.3527 10.088 0.9167 1.9483 3.0877 2.9301 5.6994 3.2352 4.7779 0.7829 10.79-0.8337 12.409-2.7868 0.87-1.0491 1.0908-2.999-0.2725-3.8171z"/>
<rect x="39" y="31" width="19" height="12" rx="3.5" fill="#fff" stroke="#0c0c11" stroke-width="1.5"/>
<text x="48.5" y="40.2" text-anchor="middle" font-family="Walsheim,sans-serif" font-weight="700"
font-size="8.5" fill="#0c0c11">TV</text></svg>"""

_DIR = os.path.dirname(__file__)
ASSETS = {"/a/sans.ttf": ("fonts/GTWalsheimPro-Regular.ttf", "font/ttf"),
          "/a/bold.ttf": ("fonts/GTWalsheimPro-Bold.ttf", "font/ttf"),
          "/a/impact.ttf": ("fonts/ZZImpact.ttf", "font/ttf"),
          "/a/mono.ttf": ("fonts/GeistMono-Medium.ttf", "font/ttf")}


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
<div class="top">{MARK}<b><strong>Zeker</strong>Zichtbaar</b><small class="mono">Geen verbinding</small></div>
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
</main></body></html>"""


def captive_json(base):
    """RFC 8908 API answer for the option 114 URI: captive, and where the page is."""
    return json.dumps({"captive": True, "user-portal-url": base + "/"}).encode()


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
        if "application/captive+json" in (self.headers.get("Accept") or ""):
            return self._send(200, captive_json(self.server.base), [("Content-Type", "application/captive+json")])
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
        self._page(notice=connecting_notice(ssid))


def connecting_notice(ssid):
    """What the page says after "Verbinden": the hotspot goes away, so this page stops answering."""
    return (f"Het scherm maakt nu verbinding met <b>{html.escape(ssid)}</b>.<br><br>"
            "Het instelnetwerk gaat zo uit. Je telefoon valt dan terug op zijn eigen wifi of mobiele "
            "data, en deze pagina reageert niet meer. Dat is goed: je kunt hem sluiten.<br><br>"
            "<b>Kijk op de tv.</b> Lukt het, dan verschijnt daar een koppelcode. Lukt het niet, dan komt "
            "het instelnetwerk binnen een minuut terug met de fout op de tv. Verbind dan opnieuw en "
            "probeer het nog eens.")


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
    assert 'lang="nl"' in p and ">Verbinden</button>" in p and "Lijst vernieuwen" in p and "🔒" in p
    n = connecting_notice("Kantine <5G>")
    assert "Kantine &lt;5G&gt;" in n and "instelnetwerk gaat zo uit" in n and "Kijk op de tv" in n
    assert "eigen wifi" in n and "binnen een minuut terug" in n
    assert json.loads(captive_json("http://10.42.0.1")) == {"captive": True,
                                                           "user-portal-url": "http://10.42.0.1/"}
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
    api = urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{port}/", headers={"Accept": "application/captive+json"}))
    assert api.headers["Content-Type"] == "application/captive+json"
    assert json.loads(api.read())["user-portal-url"] == f"http://127.0.0.1:{port}/"
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}/connect",
                               data=b"ssid=Kantine&password=secret123").read().decode()
    assert "instelnetwerk gaat zo uit" in r and "Kijk op de tv" in r
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
