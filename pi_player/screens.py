"""Setup screens rendered as 1920x1080 PNGs (Pillow + qrcode), shown on HDMI by gst.Display.

Same look as the Chromium player's tv screens (CMS `resources/css/player.css`, house style "Samen"):
dark, green accent, Bricolage Grotesque / Geist / Geist Mono (OFL, bundled in `fonts/`). Sizes are
the CSS cqmin values times 10.8 (1080 px short side). A PNG cannot move, so the animations are drawn
as their reduced-motion still frame.

On a panel hung at 90/270 (CMS `screens.rotation`, clockwise) the screen is laid out upright as
1080x1920 and the PNG is turned back counter-clockwise, so kmssink still gets 1920x1080. Centred
screens (identify, waiting, connecting, no network) are drawn natively in portrait; the two-column
ones (pairing code + QR, wifi setup) are drawn landscape and scaled into the portrait frame.

Preview: python3 -m pi_player.screens OUT_DIR
"""
import functools
import os
import time
from urllib.parse import urlparse

W, H = 1920, 1080
BG, FG, SOFT, MUTED = (7, 8, 10), (242, 244, 246), (201, 209, 219), (163, 171, 183)
DIM, LINE, ACCENT, ON_ACCENT = (107, 115, 128), (58, 65, 76), (61, 220, 132), (4, 32, 14)
WARN, ERROR = (245, 184, 61), (255, 110, 100)

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONTS = {
    "sans": "Geist-Regular.ttf",
    "semi": "Geist-SemiBold.ttf",
    "display": "BricolageGrotesque-Bold.ttf",
    "mono": "GeistMono-Medium.ttf",
}
FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@functools.lru_cache(maxsize=64)
def _font(size, kind="sans"):
    from PIL import ImageFont
    for p in (os.path.join(FONT_DIR, FONTS[kind]), FALLBACK):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _text(d, xy, s, size, fill=FG, kind="sans", anchor="la", fit=None, track=0.0):
    """Draw s, returns its width. fit: max width in px, the font shrinks until the text fits (long
    screen names in portrait). track: letter spacing in em, like CSS. fill may be a function of the
    character (the green dash in a pairing code)."""
    font = _font(size, kind)

    def width():
        return d.textlength(s, font=font) + track * size * max(0, len(s) - 1)

    while fit and size > 12 and width() > fit:
        size = int(size * 0.9)
        font = _font(size, kind)
    w = width()
    if not track and not callable(fill):
        d.text(xy, s, font=font, fill=fill, anchor=anchor)
        return w
    x, y = xy
    x -= {"l": 0, "m": w / 2, "r": w}[anchor[0]]
    # One baseline for the whole string: per character, "lm" or "lt" would shift each glyph.
    y += d.textbbox((0, 0), s, font=font, anchor="l" + anchor[1])[1] - d.textbbox((0, 0), s, font=font, anchor="ls")[1]
    for ch in s:
        d.text((x, y), ch, font=font, fill=fill(ch) if callable(fill) else fill, anchor="ls")
        x += d.textlength(ch, font=font) + track * size
    return w


def _wrap(d, s, size, kind, max_w):
    lines, line = [], ""
    for word in s.split():
        test = (line + " " + word).strip()
        if line and d.textlength(test, font=_font(size, kind)) > max_w:
            lines.append(line)
            line = word
        else:
            line = test
    return lines + [line] if line else lines


def _sprite(img, x, y, size, paint, k=4):
    """Antialiased shapes: paint(draw, k) draws at k times the size, pasted downsampled at (x, y)."""
    from PIL import Image, ImageDraw
    big = Image.new("RGBA", (size[0] * k, size[1] * k), (0, 0, 0, 0))
    paint(ImageDraw.Draw(big), k)
    small = big.resize(size, Image.LANCZOS)
    img.paste(small, (int(x), int(y)), small)


ZZ_PINK = (255, 0, 128)
ZZ_LOGO = os.path.join(os.path.dirname(__file__), "zz-logo.png")  # CMS zeker-zichtbaar-credit.tsx


def _mark(img, x, y, size):
    """The Narrowcasting mark (CMS app-logo-icon.tsx / favicon.svg): a green rounded square with a
    screen playing and a Zeker Zichtbaar pink live dot."""
    def paint(d, k):
        s = size * k
        t = s * 0.6 / 32  # the CMS icon: its 32-unit drawing at 60 % of the tile, centred
        o = (s - 32 * t) / 2

        def p(a, b):
            return o + (a - 4) * t, o + (b - 4.5) * t
        w = max(1, round(3 * t))
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.195, fill=ACCENT)
        d.rounded_rectangle([*p(6, 9), *p(34, 27.5)], radius=3.5 * t, outline=ON_ACCENT, width=w)
        d.line([p(14.5, 32.5), p(25.5, 32.5)], fill=ON_ACCENT, width=w)
        for end in (p(14.5, 32.5), p(25.5, 32.5)):  # round line caps
            d.ellipse([end[0] - w / 2, end[1] - w / 2, end[0] + w / 2, end[1] + w / 2], fill=ON_ACCENT)
        d.polygon([p(17.5, 14.25), p(17.5, 22.25), p(24, 18.25)], fill=ON_ACCENT)
        cx, cy = p(33, 10)
        ring, dot = 5.75 * t, 4.5 * t
        d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], fill=ACCENT)
        d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=ZZ_PINK)
    _sprite(img, x, y, (size, size), paint)


def _zz(img, d, x, y, size, fill, right=False):
    """"Zeker Zichtbaar" with the house logo (the pink blob), vertically centred on y; from x, or
    ending at x with right=True. size = the text size, the blob is 1.5 times as high."""
    from PIL import Image
    font = _font(size, "display")
    h = round(size * 1.5)
    tw = d.textlength("Zeker Zichtbaar", font=font)
    try:
        with Image.open(ZZ_LOGO) as lg:
            lg = lg.convert("RGBA").resize((round(h * lg.width / lg.height), h), Image.LANCZOS)
    except OSError:
        lg = None  # missing file: only the name
    lw = lg.width + round(size * 0.6) if lg else 0
    left = x - lw - tw if right else x
    if lg:
        img.paste(lg, (int(left), int(y - h / 2)), lg)
    _text(d, (left + lw, y), "Zeker Zichtbaar", size, fill, "display", "lm")


def _canvas(portrait=False, glow=True):
    """Dark background with the two soft blobs (green top left, blue bottom right)."""
    from PIL import Image
    w, h = (H, W) if portrait else (W, H)
    img = Image.new("RGB", (w, h), BG)
    if not glow:
        return img
    # radial_gradient: 0 in the centre, 255 at the edge; the CSS gradients fade out at 65 %.
    for (cx, cy, r, color, alpha) in ((0.1 * w, 0.05 * h, 570, ACCENT, 0.3),
                                      (w - 0.17 * w, h + 0.03 * h, 615, (37, 70, 240), 0.28)):
        mask = Image.radial_gradient("L").point(lambda v: int(255 * alpha * max(0.0, 1 - v / 166)))
        mask = mask.resize((2 * r, 2 * r), Image.BILINEAR)
        full = Image.new("L", (w, h), 0)
        full.paste(mask, (int(cx - r), int(cy - r)))
        img.paste(color, mask=full)
    return img


def _logo(img, logo):
    """Customer logo (config `logo`) top left; False when there is none, then the brand shows."""
    from PIL import Image
    if not (logo and os.path.exists(logo)):
        return False
    try:
        with Image.open(logo) as lg:
            lg = lg.convert("RGBA")
            lg.thumbnail((360, 140))
            img.paste(lg, (72, 56), lg)
        return True
    except OSError:
        return False  # broken logo file: the brand instead


def _qr(img, data, x, y, size):
    """A white rounded card of size x size px at (x, y) with a QR code for data. Uses the module
    matrix directly so it works across qrcode versions (Bookworm ships 7.4)."""
    import qrcode
    from PIL import ImageDraw
    _sprite(img, x, y, (size, size), lambda d, k: d.rounded_rectangle(
        [0, 0, size * k - 1, size * k - 1], radius=24 * k, fill=(255, 255, 255)))
    q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(data)
    q.make(fit=True)
    m = q.get_matrix()
    inner = size - 2 * 32
    cell = inner // len(m)
    ox = x + (size - cell * len(m)) // 2
    oy = y + (size - cell * len(m)) // 2
    d = ImageDraw.Draw(img)
    for r, row in enumerate(m):
        for c, on in enumerate(row):
            if on:
                d.rectangle([ox + c * cell, oy + r * cell, ox + (c + 1) * cell - 1, oy + (r + 1) * cell - 1],
                            fill=(0, 0, 0))


def _dots(d, x, y):
    """Three dots, "still busy" (still frame of the blinking dots)."""
    for i, a in enumerate((0.35, 0.65, 1.0)):
        cx = x + i * 16.5
        d.ellipse([cx, y - 4.5, cx + 9, y + 4.5], fill=MUTED + (int(255 * a),))


def _notice(img, footer, logo, title, kicker=None, text=None, note=None, busy=False, warn=False):
    """Full-screen message (canvas "Tv - Rustscherm"): beacon, kicker, title, text, note, with the
    tiles and scan line at the bottom and the brand and footer in the corners."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    cx = w // 2
    title_size = int(min(126, 0.09 * w))
    note_lines = _wrap(d, note, 26, "sans", min(w - 170, 1000)) if note else []
    rows = [("beacon", 245, 30)]  # the outer ring is 1.7 x 144 px
    if kicker:
        rows.append(("kicker", 30, 18))
    rows.append(("title", int(title_size * 1.1), 18))
    if text:
        rows.append(("text", 42, 18))
    rows += [("note", 36, 0) for _ in note_lines]
    y = (h - sum(r[1] + r[2] for r in rows) + rows[-1][2]) / 2
    for kind, height, gap in rows:
        mid = y + height / 2
        if kind == "beacon":
            _beacon(img, cx, mid)
        elif kind == "kicker":
            _text(d, (cx, mid), kicker.upper(), 24, WARN if warn else ACCENT, "mono", "mm", track=0.14)
        elif kind == "title":
            _text(d, (cx, mid), title, title_size, FG, "display", "mm", fit=w - 170)
        elif kind == "text":
            tw = d.textlength(text, font=_font(33))
            shift = 26 if busy else 0
            _text(d, (cx - shift, mid), text, 33, MUTED, anchor="mm")
            if busy:
                _dots(d, cx - shift + tw / 2 + 15, mid)
        else:
            _text(d, (cx, mid), note_lines.pop(0), 26, DIM, anchor="mm")
        y += height + gap
    _tiles(d, w, h)
    if not _logo(img, logo):
        _zz(img, d, 72, 74, 27, (200, 202, 204))
    if footer:
        _text(d, (w - 72, 63), footer.upper(), 21, DIM, "mono", "rt", fit=w // 2, track=0.08)
    return img


def _beacon(img, cx, cy):
    """The mark with two rings around it (still frame of the pulsing rings)."""
    size = 300

    def paint(d, k):
        c = size * k / 2
        for scale, alpha in ((1.2, 0.35), (1.7, 0.15)):
            r = 72 * scale * k
            d.ellipse([c - r, c - r, c + r, c + r], outline=ACCENT + (int(255 * alpha),), width=3 * k)
    _sprite(img, cx - size / 2, cy - size / 2, (size, size), paint)
    _mark(img, int(cx - 54), int(cy - 54), 108)


def _tiles(d, w, h):
    """The row of screen tiles above the bottom edge and the scan line on it."""
    sizes = [(132, 74)] * 3 + [(45, 80)] + [(132, 74)] * 2
    x = (w - sum(s[0] for s in sizes) - 21 * (len(sizes) - 1)) / 2
    bottom = h - 96
    for i, (tw, th) in enumerate(sizes):
        alpha = 0.55 if i == 2 else 0.2
        d.rounded_rectangle([x, bottom - th, x + tw, bottom], radius=6, fill=ACCENT + (int(255 * alpha),))
        x += tw + 21
    d.rectangle([0, h - 5, w, h], fill=(255, 255, 255, 15))
    start, length = 0.3 * w, 0.4 * w
    for i in range(int(length)):
        a = 1 - abs(i / length * 2 - 1)
        d.line([(start + i, h - 5), (start + i, h)], fill=ACCENT + (int(255 * a),))


def _head(img, d, logo, status, warn=False):
    """Two-column screens: brand top left, connection status top right."""
    if not _logo(img, logo):
        _mark(img, 120, 96, 66)
        _text(d, (207, 129), "Narrowcasting", 33, FG, "display", "lm")
    dot = WARN if warn else ACCENT
    w = _text(d, (W - 120, 129), status, 23, MUTED, "mono", "rm", track=0.08)
    d.ellipse([W - 120 - w - 30, 122, W - 120 - w - 16, 136], fill=dot)


def _step(d, x, y, n, s):
    """A numbered step: the number in a ring, then the text. y = the middle of the line."""
    d.ellipse([x, y - 27, x + 54, y + 27], outline=LINE, width=3)
    _text(d, (x + 27, y), str(n), 24, FG, "mono", "mm")
    _text(d, (x + 78, y), s, 33, SOFT, anchor="lm", fit=W - 120 - 450 - 108 - x - 78)


def _foot(img, d, footer):
    """Footer text bottom left, Zeker Zichtbaar bottom right (two-column screens)."""
    if footer:
        _text(d, (120, H - 48), footer.upper(), 20, DIM, "mono", "ls", fit=W - 640, track=0.08)
    _zz(img, d, W - 120, H - 60, 24, (200, 202, 204), right=True)


def portrait(rotation):
    return rotation in (90, 270)


def _turn(img, rotation):
    """Upright layout -> what the 1920x1080 panel must show when it hangs at `rotation` (clockwise):
    the picture turns back counter-clockwise, like the Chromium player's CSS rotate(-rotation)."""
    from PIL import Image
    if not portrait(rotation):
        return img
    if img.width > img.height:  # landscape layout on a portrait panel: scale into the upright frame
        frame = Image.new("RGB", (H, W), BG)
        small = img.resize((H, H * H // W), Image.LANCZOS)
        frame.paste(small, (0, (W - small.height) // 2))
        img = frame
    return img.rotate(rotation, expand=True)  # PIL turns counter-clockwise


def _save(img, path, rotation=0):
    img = _turn(img, rotation)
    tmp = path + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, path)
    return path


def pairing(path, code=None, pair_url=None, expires_at=None, footer="", logo=None, rotation=0):
    """Steps, big code and a QR to pair_url (canvas "Tv - Koppelen"). code None -> 'connecting'."""
    from PIL import ImageDraw
    if not code:
        img = _notice(_canvas(portrait(rotation)), footer, logo, "Verbinden", text="Even geduld",
                      note="Zo verschijnt hier een koppelcode.", busy=True)
        return _save(img, path, rotation)
    img = _canvas()
    d = ImageDraw.Draw(img, "RGBA")
    _head(img, d, logo, "VERBONDEN")
    host = urlparse(pair_url).netloc if pair_url else ""
    y = 222 + (762 - 590) // 2
    _text(d, (120, y), "Koppel dit scherm", 84, FG, "display", "lt")
    y += 88 + 54 + 27
    _step(d, 120, y, 1, f"Ga naar {host}/schermen" if host else "Ga in het beheer naar Schermen")
    y += 54 + 27
    _step(d, 120, y, 2, "Vul deze code in")
    y += 27 + 54
    _text(d, (114, y), code, 168, lambda ch: ACCENT if ch == "-" else (255, 255, 255), "mono", "lt",
          fit=W - 120 - 450 - 108 - 120, track=0.14)
    if expires_at:
        until = time.strftime("%H:%M", time.localtime(expires_at))
        _text(d, (120, y + 168 + 54), f"Geldig tot {until}, daarna verschijnt vanzelf een nieuwe code",
              25, MUTED, "mono", "lt", fit=W - 120 - 450 - 108 - 120)
    if pair_url:
        qx, qy = W - 120 - 450, 222 + (762 - 507) // 2
        _qr(img, pair_url, qx, qy, 450)
        _text(d, (qx + 225, qy + 450 + 27), "Of scan met je telefoon", 27, SOFT, anchor="mt")
    _foot(img, d, footer)
    return _save(img, path, rotation)


def waiting(path, name=None, footer="", logo=None, rotation=0):
    """Paired, nothing to play yet (canvas "Tv - Rustscherm")."""
    img = _notice(_canvas(portrait(rotation)), footer, logo, name or "Gekoppeld",
                  kicker="Gekoppeld als" if name else None, text="Wacht op een playlist",
                  note="Kies in het beheer onder Schermen welke playlist hier draait en zorg dat hij "
                       "gepubliceerd is.", busy=True)
    return _save(img, path, rotation)


def identify(path, name, footer="", logo=None, rotation=0):
    """"Scherm identificeren": the screen name, huge, so you can see which screen is which (canvas
    "Tv - Identificeren", without the countdown: the PNG stays until the CMS stops identifying)."""
    from PIL import ImageDraw
    img = _canvas(portrait(rotation), glow=False)
    img.paste((4, 6, 8), (0, 0, *img.size))
    d = ImageDraw.Draw(img)
    w, h = img.size
    cx, size = w // 2, int(min(225, 0.14 * w))
    pill_w = 54 + 33 + 18 + d.textlength("DIT SCHERM", font=_font(27, "mono")) + 0.12 * 27 * 9
    top = h // 2 - (66 + 42 + size) // 2

    def pill(dr, k):
        dr.rounded_rectangle([0, 0, pill_w * k - 1, 66 * k - 1], radius=33 * k, outline=ACCENT, width=3 * k)
        ex, ey, u = 27 * k, 17 * k, 33 / 24 * k  # eye icon, 24-unit viewbox scaled to 33 px
        r, c = 10.64 * u, 3 * u
        dr.arc([ex + (12 * u - r), ey + 15.64 * u - r, ex + 12 * u + r, ey + 15.64 * u + r], 200, 340,
               fill=ACCENT, width=round(2 * u))
        dr.arc([ex + (12 * u - r), ey + 8.36 * u - r, ex + 12 * u + r, ey + 8.36 * u + r], 20, 160,
               fill=ACCENT, width=round(2 * u))
        dr.ellipse([ex + 12 * u - c, ey + 12 * u - c, ex + 12 * u + c, ey + 12 * u + c], outline=ACCENT,
                   width=round(2 * u))
    _sprite(img, cx - pill_w / 2, top, (int(pill_w), 66), pill)
    _text(d, (cx - pill_w / 2 + 27 + 33 + 18, top + 33), "DIT SCHERM", 27, ACCENT, "mono", "lm", track=0.12)
    _text(d, (cx, top + 66 + 42 + size / 2), name, size, (255, 255, 255), "display", "mm", fit=w - 130)
    if footer:
        _text(d, (cx, h - 60), footer.upper(), 21, DIM, "mono", "ms", fit=w - 160, track=0.08)
    return _save(img, path, rotation)


UPDATE_TEXT = "Aan het updaten..."


def update_label(path, rotation=0):
    """The small "Aan het updaten..." label (house dark, green dot, Geist SemiBold ~3 % of the short
    side) that gst.Display puts bottom-left on its own plane over the playing video during a player
    update. Transparent around a rounded box, alpha PREMULTIPLIED (the vc4 plane default blend
    mode), turned like the screens for a panel at 90/270 (the plane itself cannot turn 90)."""
    from PIL import Image, ImageChops, ImageDraw
    size = 32
    font = _font(size, "semi")
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), UPDATE_TEXT, font=font)
    pad_x, pad_y, dot, gap = round(size * 0.75), round(size * 0.45), round(size * 0.35), round(size * 0.45)
    w, h = pad_x + dot + gap + (right - left) + pad_x, (bottom - top) + 2 * pad_y

    def box(d, k):
        d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k * 0.3, fill=BG + (224,))
        cy = h * k / 2
        d.ellipse([pad_x * k, cy - dot * k / 2, (pad_x + dot) * k, cy + dot * k / 2], fill=ACCENT + (255,))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    _sprite(img, 0, 0, (w, h), box)
    ImageDraw.Draw(img).text((pad_x + dot + gap - left, pad_y - top), UPDATE_TEXT, font=font, fill=FG + (255,))
    r, g, b, a = img.split()
    img = Image.merge("RGBA", [ImageChops.multiply(c, a) for c in (r, g, b)] + [a])
    if portrait(rotation):
        img = img.rotate(rotation, expand=True)  # counter-clockwise, like _turn
    tmp = path + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, path)
    return path


def wifi_setup(path, ssid=None, password=None, status=None, error=None, footer="", logo=None,
               portal_url="http://10.42.0.1", rotation=0, setup_url=None):
    """Wifi setup. With setup_url: a QR to the App Clip (Bluetooth) and the hotspot SSID + password as
    the route for other phones. Without: a join-QR for the hotspot plus a second QR (and the address) to
    the setup page for phones that don't open it by themselves (Private DNS, mobile data). Nothing to
    offer (no hotspot, no Bluetooth) or busy without a hotspot: a notice with the status/error, or
    'connect ethernet'."""
    from PIL import ImageDraw
    from .netman import wifi_qr
    if not (ssid or setup_url) or (status and not ssid):
        img = _notice(_canvas(portrait(rotation)), footer, logo, status or "Geen netwerk", kicker="Wifi",
                      text=error or ("Het instelnetwerk komt zo terug" if status
                                     else "Sluit een netwerkkabel aan, of wacht op de wifi-instelling"),
                      busy=bool(status) and not error, warn=not status or bool(error))
        return _save(img, path, rotation)
    img = _canvas()
    d = ImageDraw.Draw(img, "RGBA")
    _head(img, d, logo, "GEEN VERBINDING", warn=True)
    if setup_url:  # App Clip QR; the hotspot (if up) as the route for other phones
        fit = W - 120 - 480 - 108 - 198
        y = 236
        _text(d, (120, y), "Wifi instellen", 84, FG, "display", "lt")
        y += 88 + 45 + 27
        _step(d, 120, y, 1, "Scan de QR-code met de camera van je iPhone")
        y += 27 + 18
        _text(d, (198, y), "‘Scherm instellen’ opent en zet de wifi van het scherm voor je in", 27, MUTED,
              "sans", "lt", fit=fit)
        y += 27 + 54 + 27
        if ssid:
            _step(d, 120, y, 2, "Andere telefoon? Verbind met het wifi-netwerk:")
            y += 27 + 18
            _text(d, (198, y), ssid, 56, (255, 255, 255), "mono", "lt", fit=fit)
            y += 56 + 24
            _text(d, (198, y + 24), "WACHTWOORD", 22, MUTED, "mono", "lm", track=0.14)
            _text(d, (390, y + 24), password, 44, FG, "mono", "lm")
            y += 48 + 36
            _text(d, (198, y), f"Geen pagina? Open {portal_url} in je browser", 25, MUTED, "mono", "lt", fit=fit)
            y += 25
        y += 38
        qx, qy = W - 120 - 480, 222 + (762 - 537) // 2
        _qr(img, setup_url, qx, qy, 480)
        _text(d, (qx + 240, qy + 480 + 27), "Scan met je camera", 27, SOFT, anchor="mt")
        if error:
            _text(d, (198, y), error, 33, ERROR, "semi", "lt", fit=fit)
        elif status:
            _text(d, (198, y), status, 33, ACCENT, "semi", "lt", fit=fit)
        _foot(img, d, footer)
        return _save(img, path, rotation)
    q = 320  # two QR cards stacked in the right column: 1 = join the hotspot, 2 = open the page
    qx = W - 120 - q
    fit = qx - 108 - 198
    y = 222
    _text(d, (120, y), "Wifi instellen", 84, FG, "display", "lt")
    y += 88 + 45 + 27
    _step(d, 120, y, 1, "Scan code 1, of maak verbinding met:")
    y += 27 + 18
    _text(d, (198, y), ssid, 72, (255, 255, 255), "mono", "lt", fit=fit)
    y += 72 + 24
    _text(d, (198, y + 27), "WACHTWOORD", 22, MUTED, "mono", "lm", track=0.14)
    _text(d, (390, y + 27), password, 48, FG, "mono", "lm")
    y += 54 + 18
    _text(d, (198, y), "De instelpagina opent daarna vanzelf op je telefoon", 25, MUTED, "mono", "lt", fit=fit)
    y += 25 + 45 + 27
    _step(d, 120, y, 2, "Opent er niets? Scan code 2, of ga naar:")
    y += 27 + 18
    _text(d, (198, y), portal_url, 56, ACCENT, "mono", "lt", fit=fit)
    y += 56 + 36
    if error:
        _text(d, (198, y), error, 33, ERROR, "semi", "lt", fit=fit)
    elif status:
        _text(d, (198, y), status, 33, ACCENT, "semi", "lt", fit=fit)
    for i, (data, label) in enumerate(((wifi_qr(ssid, password), "1  Verbind met de wifi"),
                                       (portal_url, "2  Open de instelpagina"))):
        qy = 196 + i * (q + 84)
        _qr(img, data, qx, qy, q)
        _text(d, (qx + q // 2, qy + q + 18), label, 27, SOFT, anchor="mt")
    _foot(img, d, footer)
    return _save(img, path, rotation)


def _selfcheck():
    from PIL import Image
    red = (255, 0, 0)
    up = Image.new("RGB", (H, W), BG)  # upright portrait layout with a red band at its top
    up.paste(red, (0, 0, H, 40))
    for rot, edge in ((90, (5, H // 2)), (270, (W - 5, H // 2))):
        out = _turn(up, rot)
        assert out.size == (W, H), out.size
        # hung clockwise (90): the top of the content sits on the panel's left edge, which is up
        assert out.getpixel(edge) == red, (rot, out.getpixel(edge))
    assert _turn(up, 0) is up
    wide = Image.new("RGB", (W, H), red)  # landscape layout on a portrait panel: scaled, black bars
    out = _turn(wide, 90)
    assert out.size == (W, H) and out.getpixel((W // 2, H // 2)) == red and out.getpixel((10, H // 2)) == BG
    for kind in FONTS:  # the bundled fonts load, not the DejaVu fallback
        assert _font(20, kind).getname()[0] != "DejaVu Sans", kind
    assert os.path.exists(ZZ_LOGO), ZZ_LOGO
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:  # update label: upright for the viewer, premultiplied
        flat = Image.open(update_label(os.path.join(tmp, "u0.png"))).convert("RGBA")
        assert flat.width > 4 * flat.height and flat.height < 80, flat.size
        assert flat.getpixel((0, 0))[3] == 0  # rounded corner: transparent, so the video shows
        px = flat.tobytes()
        assert all(px[i + c] <= px[i + 3] for i in range(0, len(px), 4) for c in range(3))  # premultiplied
        dot = (round(32 * 0.75) + 5, flat.height // 2)
        assert flat.getpixel(dot)[1] > 150, flat.getpixel(dot)  # green dot on the left, text to its right
        for rot, at in ((90, lambda i: (i.width // 2, i.height - 1 - dot[0])), (270, lambda i: (i.width - 1 - dot[1], dot[0]))):
            turned = Image.open(update_label(os.path.join(tmp, f"u{rot}.png"), rot)).convert("RGBA")
            assert turned.size == (flat.height, flat.width), (rot, turned.size)
            # the label's start (green dot) is at the viewer's left: the panel's bottom (90) / top (270)
            assert turned.getpixel(at(turned)) == flat.getpixel(dot), (rot, turned.getpixel(at(turned)))
    print("screens self-check OK")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["check"]:
        _selfcheck()
        sys.exit()
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    f = "pi-a1b2c3d4e5f6a7b8 · 0.1.0"
    url = "https://narrowcasting.zekerzichtbaar.nl/schermen?code=K7Q-4M2"
    print(pairing(os.path.join(out, "pairing.png"), "K7Q-4M2", url, time.time() + 600, f))
    print(pairing(os.path.join(out, "pairing-connecting.png"), footer=f))
    print(waiting(os.path.join(out, "waiting.png"), "Kantine links", f))
    print(waiting(os.path.join(out, "waiting-noname.png"), None, f))
    print(identify(os.path.join(out, "identify.png"), "Kantine links", f))
    for r in (0, 90, 270):
        print(update_label(os.path.join(out, f"update-label-{r}.png"), r))
    clip = "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw"
    print(wifi_setup(os.path.join(out, "wifi-clip.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f, setup_url=clip))
    print(wifi_setup(os.path.join(out, "wifi-clip-error.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                     setup_url=clip, error="Verbinden met Kantine mislukt: Verkeerd wachtwoord?"))
    print(wifi_setup(os.path.join(out, "wifi-clip-noap.png"), footer=f, setup_url=clip,
                     error="Het instelnetwerk start niet. Sluit een netwerkkabel aan."))
    print(wifi_setup(os.path.join(out, "wifi.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-error.png"), "Narrowcast-1E2F", "k7q4m2xa",
                     error="Verbinden met Kantine mislukt: Verkeerd wachtwoord?", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-busy.png"), status="Verbinden met Kantine…", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-none.png"), footer=f))
    for r in (90, 270):  # as sent to a panel hung at r: turn your head (or the image) to read it
        print(pairing(os.path.join(out, f"pairing-{r}.png"), "K7Q-4M2", url, time.time() + 600, f, rotation=r))
        print(waiting(os.path.join(out, f"waiting-{r}.png"), "Kantine links bij de bar", f, rotation=r))
        print(identify(os.path.join(out, f"identify-{r}.png"), "Kantine links bij de bar", f, rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-none-{r}.png"), footer=f, rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-{r}.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f, rotation=r))
