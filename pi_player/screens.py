"""Setup screens rendered as 1920x1080 PNGs (Pillow + qrcode), shown by mpv like any file.

Preview: python3 -m pi_player.screens OUT_DIR
"""
import os
import time

W, H = 1920, 1080
BG, FG, MUTED, ACCENT = (18, 20, 24), (240, 240, 240), (150, 155, 165), (255, 196, 0)
ERROR = (255, 110, 100)

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental"]
FONTS = {False: ["DejaVuSans.ttf", "Arial.ttf"], True: ["DejaVuSans-Bold.ttf", "Arial Bold.ttf"]}


def _font(size, bold=False):
    from PIL import ImageFont
    for d in FONT_DIRS:
        for name in FONTS[bold]:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _qr(img, data, x, y, size):
    """Draw a QR code for data into img, size x size px, top-left at (x, y). Uses the module
    matrix directly so it works across qrcode versions (Bookworm ships 7.4)."""
    import qrcode
    from PIL import ImageDraw
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(data)
    q.make(fit=True)
    m = q.get_matrix()
    cell = size // len(m)
    size = cell * len(m)
    d = ImageDraw.Draw(img)
    d.rectangle([x, y, x + size - 1, y + size - 1], fill=(255, 255, 255))
    for r, row in enumerate(m):
        for c, on in enumerate(row):
            if on:
                d.rectangle([x + c * cell, y + r * cell, x + (c + 1) * cell - 1, y + (r + 1) * cell - 1],
                            fill=(0, 0, 0))
    return size


def _canvas(logo):
    from PIL import Image
    img = Image.new("RGB", (W, H), BG)
    if logo and os.path.exists(logo):
        try:
            with Image.open(logo) as lg:
                lg = lg.convert("RGBA")
                lg.thumbnail((360, 140))
                img.paste(lg, (80, 70), lg)
        except OSError:
            pass  # broken logo file: just leave it out
    return img


def _text(d, xy, s, size, fill=FG, bold=False, anchor="la"):
    d.text(xy, s, font=_font(size, bold), fill=fill, anchor=anchor)


def _footer(d, footer):
    if footer:
        _text(d, (W // 2, H - 60), footer, 26, MUTED, anchor="ms")


def _save(img, path):
    tmp = path + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, path)
    return path


def pairing(path, code=None, pair_url=None, expires_at=None, footer="", logo=None):
    """Big code + QR to pair_url. code None -> 'connecting to CMS' placeholder."""
    from PIL import ImageDraw
    img = _canvas(logo)
    d = ImageDraw.Draw(img)
    if not code:
        _text(d, (W // 2, H // 2 - 20), "Connecting to the CMS…", 72, bold=True, anchor="mm")
        _text(d, (W // 2, H // 2 + 70), "A pairing code will appear here.", 40, MUTED, anchor="mm")
        _footer(d, footer)
        return _save(img, path)
    left = 160
    _text(d, (left, 260), "Pair this screen", 80, bold=True)
    _text(d, (left, 380), "Scan the QR code, or enter this code in the CMS:", 40, MUTED)
    _text(d, (left - 8, 450), code, 220, ACCENT, bold=True)
    if expires_at:
        _text(d, (left, 740), "Code valid until " + time.strftime("%H:%M", time.localtime(expires_at))
              + " — a new one appears automatically.", 34, MUTED)
    if pair_url:
        _text(d, (left, 800), pair_url, 30, MUTED)
        _qr(img, pair_url, W - 160 - 560, (H - 560) // 2, 560)
    _footer(d, footer)
    return _save(img, path)


def waiting(path, name=None, footer="", logo=None):
    from PIL import ImageDraw
    img = _canvas(logo)
    d = ImageDraw.Draw(img)
    _text(d, (W // 2, H // 2 - 30), "Waiting for content", 96, bold=True, anchor="mm")
    _text(d, (W // 2, H // 2 + 80), name or "Paired — publish something in the CMS for this screen.",
          48, ACCENT if name else MUTED, anchor="mm")
    _footer(d, footer)
    return _save(img, path)


def wifi_setup(path, ssid=None, password=None, status=None, error=None, footer="", logo=None,
               portal_url="http://10.42.0.1"):
    """Hotspot SSID + password + join-QR. ssid None -> no hotspot (no wifi adapter / dev box /
    hotspot briefly down): show status/error, or 'connect ethernet'."""
    from PIL import ImageDraw
    from .netman import wifi_qr
    img = _canvas(logo)
    d = ImageDraw.Draw(img)
    if not ssid:
        _text(d, (W // 2, H // 2 - 30), status or "No network", 96 if not status else 72, bold=True,
              anchor="mm")
        _text(d, (W // 2, H // 2 + 80), error or ("The setup network comes back in a moment." if status
              else "Connect an ethernet cable, or wait for wifi setup."), 44, MUTED, anchor="mm")
        _footer(d, footer)
        return _save(img, path)
    left = 160
    _text(d, (left, 220), "Set up wifi", 80, bold=True)
    _text(d, (left, 340), "1.  Scan the QR code, or join this wifi network:", 38, MUTED)
    _text(d, (left - 4, 400), ssid, 96, ACCENT, bold=True)
    _text(d, (left, 530), "Password", 34, MUTED)
    _text(d, (left + 180, 515), password, 64, FG, bold=True)
    _text(d, (left, 640), "2.  A setup page opens on your phone: pick the", 38, MUTED)
    _text(d, (left + 54, 690), "screen's network and enter its password.", 38, MUTED)
    _text(d, (left + 54, 750), "No page? Open " + portal_url + " in your browser.", 34, MUTED)
    if error:
        _text(d, (left, 850), error, 40, ERROR, bold=True)
    elif status:
        _text(d, (left, 850), status, 40, ACCENT)
    _qr(img, wifi_qr(ssid, password), W - 160 - 480, 260, 480)
    _footer(d, footer)
    return _save(img, path)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    f = "pi-a1b2c3d4e5f6a7b8 · 0.1.0"
    url = "http://cms.local:8080/pair?code=K7Q-4M2"
    print(pairing(os.path.join(out, "pairing.png"), "K7Q-4M2", url, time.time() + 600, f))
    print(pairing(os.path.join(out, "pairing-connecting.png"), footer=f))
    print(waiting(os.path.join(out, "waiting.png"), "Kantine links", f))
    print(waiting(os.path.join(out, "waiting-noname.png"), None, f))
    print(wifi_setup(os.path.join(out, "wifi.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-error.png"), "Narrowcast-1E2F", "k7q4m2xa",
                     error="Could not connect to Kantine: Wrong password?", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-busy.png"), status="Connecting to Kantine…", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-none.png"), footer=f))
