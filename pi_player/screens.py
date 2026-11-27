"""Setup screens rendered as 1920x1080 PNGs (Pillow + qrcode), shown on HDMI by gst.Display.

House style D3 (canvas "Tv (Pi 4)", https://claude.ai/artifact/JbBoFF1X8siZydxoZDW3M4): ink
background with a faint magenta blob, the Zeker Zichtbaar logo (z-blob with a TV sticker) and
wordmark, GT Walsheim Pro for text, ZZ Impact for big uppercase headings and Geist Mono for data
(TTFs in `fonts/`, see its README). Magenta only for the brand (logo, blob, step numbers,
identify); status colours as in the CMS chips. Sizes are the canvas px (1280x720 boards) times 1.5.
A PNG cannot move, so spinners are drawn as their still frame.

On a panel hung at 90/270 (CMS `screens.rotation`, clockwise) the screen is laid out upright as
1080x1920 and the PNG is turned back counter-clockwise, so kmssink still gets 1920x1080. Centred
screens (identify, waiting, connecting, no network) and pairing (canvas "Tv - koppelen staand")
are drawn natively in portrait; wifi setup is drawn landscape and scaled into the portrait frame.

Preview: python3 -m pi_player.screens OUT_DIR
"""
import functools
import os
import re
import time
from urllib.parse import urlparse

W, H = 1920, 1080
INK, SURFACE, LINE, LINE2 = (12, 12, 17), (22, 22, 29), (34, 34, 43), (42, 42, 52)
FG, SOFT, MUTED, FAINT, DASH = (255, 255, 255), (212, 212, 220), (169, 169, 182), (138, 138, 152), (110, 110, 124)
MAGENTA = (255, 0, 128)
BG = INK
# CMS status chips (zz.css .ok/.busy/.warn/.err): text colour and background tint (rgba).
TONES = {
    "ok": ((74, 222, 155), (52, 211, 153, 36)),
    "busy": ((163, 174, 255), (125, 140, 255, 41)),
    "warn": ((255, 194, 77), (255, 184, 46, 36)),
    "err": ((255, 122, 102), (255, 92, 72, 38)),
}
ERROR = TONES["err"][0]

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONTS = {
    "sans": "GTWalsheimPro-Regular.ttf",
    "medium": "GTWalsheimPro-Medium.ttf",
    "bold": "GTWalsheimPro-Bold.ttf",
    "impact": "ZZImpact.ttf",
    "mono": "GeistMono-Medium.ttf",
}
FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@functools.lru_cache(maxsize=96)
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
    character."""
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


def _imp(d, x, top, s, size, fill=FG, fit=None, anchor="l"):
    """A ZZ Impact heading (uppercase, line-height .95 like the canvas `.imp`) in a line box from
    top, caps centred in it. Returns the size used (it shrinks to fit)."""
    s = s.upper()
    while fit and size > 12 and d.textlength(s, font=_font(size, "impact")) > fit:
        size = int(size * 0.92)
    font = _font(size, "impact")
    cap = -font.getbbox("H", anchor="ls")[1]
    d.text((x, top + (0.95 * size + cap) / 2), s, font=font, fill=fill, anchor=anchor + "s")
    return size


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
    big = Image.new("RGBA", (round(size[0] * k), round(size[1] * k)), (0, 0, 0, 0))
    paint(ImageDraw.Draw(big), k)
    small = big.resize((round(size[0]), round(size[1])), Image.LANCZOS)
    img.paste(small, (round(x), round(y)), small)


# The Zeker Zichtbaar logo D3 (CMS app-logo-icon.tsx): the z-blob (viewBox 57x48) and the z, plus
# a TV sticker bottom right (viewBox 60x48 then).
BLOB = ("m49.828 9.4869c-6.4792-5.493-15.954-10.244-27.799-9.333-1.3267 0.1021-2.6662 0.25462-4.0154 0.4632"
        "-20.703 3.197-22.511 28.814-10.768 40.89 6.4852 6.6703 17.102 9.2108 30.955 1.5644 4.1535-2.2928 "
        "7.5544-4.6361 10.281-6.9866 12.181-10.5 9.5797-19.617 1.3457-26.598z")
ZED = ("m36.892 26.466c-2.2008-1.5402-6.0661-0.072-8.4833 0.2687 3.0896-2.9715 11.332-6.2127 8.0968-9.9872"
       "-3.8171-4.6348-14.86-3.3398-17.177-0.5449-0.8701 1.0491-1.0906 2.999 0.2728 3.8168 2.7266 1.9085 "
       "8.8074-0.2728 8.8074-0.2728-3.5447 2.9994-11.261 5.7257-9.3527 10.088 0.9167 1.9483 3.0877 2.9301 "
       "5.6994 3.2352 4.7779 0.7829 10.79-0.8337 12.409-2.7868 0.87-1.0491 1.0908-2.999-0.2725-3.8171z")
# The same logo as a PNG for the hotspot setup page (portal.py), made with logo_png().
ZZ_LOGO = os.path.join(os.path.dirname(__file__), "zz-logo.png")


@functools.lru_cache(maxsize=4)
def _outline(path, steps=24):
    """The polygon of an SVG path made of one relative moveto and relative cubic curves (the logo
    paths), in viewBox units."""
    tok = re.findall(r"[mcz]|-?(?:\d+\.?\d*|\.\d+)", path)
    assert tok[0] == "m" and tok[3] == "c", path
    x, y = float(tok[1]), float(tok[2])
    pts = [(x, y)]
    nums = [float(t) for t in tok[4:] if t not in "cz"]
    for i in range(0, len(nums) - 5, 6):
        x1, y1, x2, y2, ex, ey = (x + nums[i], y + nums[i + 1], x + nums[i + 2], y + nums[i + 3],
                                  x + nums[i + 4], y + nums[i + 5])
        for s in range(1, steps + 1):
            t = s / steps
            u = 1 - t
            pts.append((u ** 3 * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * ex,
                        u ** 3 * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * ey))
        x, y = ex, ey
    return tuple(pts)


def _mark(img, x, y, width, sticker=True):
    """The logo at (x, y), `width` px wide (height 0.8 x width; without the sticker 48/57). The
    sticker is white with ink on it, the dark-background variant."""
    view = 60 if sticker else 57

    def paint(d, k):
        s = width * k / view
        d.polygon([(px * s, py * s) for px, py in _outline(BLOB)], fill=MAGENTA)
        d.polygon([(px * s, py * s) for px, py in _outline(ZED)], fill=FG)
        if sticker:
            d.rounded_rectangle([38.25 * s, 30.25 * s, 58.75 * s, 43.75 * s], radius=4.25 * s, fill=INK)
            d.rounded_rectangle([39.75 * s, 31.75 * s, 57.25 * s, 42.25 * s], radius=2.75 * s, fill=FG)
            d.text((48.5 * s, 40.2 * s), "TV", font=_font(max(1, round(8.5 * s)), "bold"), fill=INK, anchor="ms")
    _sprite(img, x, y, (width, width * 48 / view), paint)


def logo_png(path=ZZ_LOGO, width=360):
    """Write the logo (dark-background variant) as a transparent PNG: python3 -m pi_player.screens logo"""
    from PIL import Image
    img = Image.new("RGBA", (width, width * 4 // 5), (0, 0, 0, 0))
    _mark(img, 0, 0, width)
    img.save(path, "PNG")
    return path


def _backdrop(img):
    """The canvas `.tv-blob`: the logo blob huge and faint (9 % magenta) in the bottom-right corner,
    partly off screen."""
    w, h = img.size
    if w > h:
        bw, right, bottom = 1020, 270, 345
    else:
        bw, right, bottom = 1120, 400, 300
    bh = bw * 48 / 57
    tint = tuple(round(0.09 * m + 0.91 * i) for m, i in zip(MAGENTA, INK))

    def paint(d, k):
        s = bw * k / 57
        d.polygon([(px * s, py * s) for px, py in _outline(BLOB)], fill=tint)
    _sprite(img, w - bw + right, h + bottom - bh, (bw, bh), paint, k=2)


def _wordmark(d, x, y, size, fill=FG, stacked=False):
    """ZEKER (bold) ZICHTBAAR (regular), 0.14em letter spacing, vertically centred on y; stacked:
    two lines. Returns the width."""
    if stacked:
        gap = size * 0.2
        w1 = _text(d, (x, y - gap / 2), "ZEKER", size, fill, "bold", "ld", track=0.14)
        w2 = _text(d, (x, y + gap / 2), "ZICHTBAAR", size, fill, "sans", "la", track=0.14)
        return max(w1, w2)
    w = _text(d, (x, y), "ZEKER", size, fill, "bold", "lm", track=0.14) + 0.14 * size
    return w + _text(d, (x + w, y), "ZICHTBAAR", size, fill, "sans", "lm", track=0.14)


def _logo(img, logo, x, y, box=(360, 100)):
    """Customer logo (config `logo`) at (x, y); False when there is none, then the brand shows."""
    from PIL import Image
    if not (logo and os.path.exists(logo)):
        return False
    try:
        with Image.open(logo) as lg:
            lg = lg.convert("RGBA")
            lg.thumbnail(box)
            img.paste(lg, (int(x), int(y)), lg)
        return True
    except OSError:
        return False  # broken logo file: the brand instead


def _brand(img, d, logo, x, y, mark_w, size, stacked=False):
    """Header top left: the customer logo, or the Zeker Zichtbaar logo with the wordmark next to it."""
    if _logo(img, logo, x, y, (360, round(mark_w * 0.8))):
        return
    _mark(img, x, y, mark_w)
    _wordmark(d, x + mark_w + round(mark_w * 0.2), y + mark_w * 0.4, size, stacked=stacked)


def _chip(img, d, right, y, label, tone, size=21):
    """A status chip (canvas `.chip`, mono) ending at x=right, vertically centred on y."""
    fg, bg = TONES[tone]
    tw = _text(d, (0, -999), label, size, fg, "mono", "lm", track=0.1)  # measure off screen
    dot, pad, h = round(size * 0.5), round(size), round(size * 2.4)
    w = pad + dot + round(size * 0.57) + tw + pad
    left = right - w

    def paint(dr, k):
        dr.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k / 2, fill=bg)
        dr.ellipse([pad * k, (h - dot) * k / 2, (pad + dot) * k, (h + dot) * k / 2], fill=fg)
    _sprite(img, left, y - h / 2, (w, h), paint)
    _text(d, (left + pad + dot + round(size * 0.57), y), label, size, fg, "mono", "lm", track=0.1)


def _spin(img, x, y, size, ring):
    """The canvas `.spin` still frame: a dark ring with its top quarter in magenta."""
    def paint(d, k):
        box = [ring * k / 2, ring * k / 2, (size - ring / 2) * k, (size - ring / 2) * k]
        d.arc(box, 0, 360, fill=LINE2, width=round(ring * k))
        d.arc(box, 225, 315, fill=MAGENTA, width=round(ring * k))
    _sprite(img, x, y, (size, size), paint)


def _qr(img, data, x, y, size):
    """A white rounded card of size x size px at (x, y) with a QR code for data. Uses the module
    matrix directly so it works across qrcode versions (Bookworm ships 7.4)."""
    import qrcode
    from PIL import ImageDraw
    _sprite(img, x, y, (size, size), lambda d, k: d.rounded_rectangle(
        [0, 0, size * k - 1, size * k - 1], radius=36 * k, fill=(255, 255, 255)))
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
                            fill=INK)


def _step(d, x, y, n, s, size=33, fit=None):
    """A numbered step (canvas `.step`): "01" in magenta mono, then the text. y = the baseline."""
    _text(d, (x, y), f"{n:02d}", round(size * 0.73), MAGENTA, "mono", "ls")
    _text(d, (x + round(size * 1.33), y), s, size, SOFT, "sans", "ls", fit=fit)


def _foot(img, d, left, footer, size=21, resume=None, pad=120, bottom=72, dot=None):
    """Bottom row: on the left the boot countdown (spinner + text) or a status text (with a dot),
    on the right the footer (device id and version) in faint mono."""
    w, h = img.size
    y = h - bottom - size * 0.7
    fw = _text(d, (w - pad, y), footer.upper(), size, FAINT, "mono", "rm", fit=w // 2) if footer else 0
    if resume:
        rs = round(size * 1.29)
        sp = round(rs * 1.22)
        ry = y if w > h else y - sp - 36  # portrait: above the footer, it does not fit next to it
        _spin(img, pad, ry - sp / 2, sp, max(3, round(sp * 0.15)))
        room = w - 2 * pad - sp - 18 - (fw + 60 if w > h else 0)
        _text(d, (pad + sp + 18, ry), resume, rs, SOFT, "sans", "lm", fit=room)
    elif left:
        x = pad
        if dot:
            r = size * 0.27
            d.ellipse([x, y - r, x + 2 * r, y + r], fill=dot)
            x += 2 * r + size * 0.57
        _text(d, (x, y), left, size, FAINT, "mono", "lm", fit=w - 2 * pad - fw - 60)


def _canvas(portrait=False, backdrop=True):
    """The ink background, with the faint blob unless backdrop=False."""
    from PIL import Image
    img = Image.new("RGB", (H, W) if portrait else (W, H), INK)
    if backdrop:
        _backdrop(img)
    return img


def _notice(img, footer, logo, title, kicker=None, text=None, note=None, busy=False, badge=None,
            error=False, resume=None, title_size=198):
    """Full-screen message, left aligned (canvas "Tv - rustscherm", "verbinden", "geen netwerk"):
    a spinner when busy, kicker, Impact title, text and note, with the brand (and a status chip)
    on top and the footer at the bottom."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    pad = 120 if w > h else 80
    _brand(img, d, logo, pad, 96, 105, 39)
    if badge:
        _chip(img, d, w - pad, 96 + 42, *badge)
    _foot(img, d, None, footer, resume=resume, pad=pad)
    size = int(min(title_size, 0.16 * w))
    note_lines = _wrap(d, note, 30, "sans", min(w - 2 * pad, 1140)) if note else []
    text_lines = _wrap(d, text, 45, "sans", w - 2 * pad) if text else []
    rows = []  # (kind, height, gap after)
    if busy:
        rows.append(("spin", 84, 33))
    if kicker:
        rows.append(("kicker", 30, 27))
    rows.append(("title", round(0.95 * size), 30))
    rows += [("text", 58, 0) for _ in text_lines]
    if text_lines:
        rows[-1] = ("text", 58, 21)
    rows += [("note", 45, 0) for _ in note_lines]
    total = sum(r[1] + r[2] for r in rows) - rows[-1][2]
    top, bottom = 96 + 84, h - 72 - 40 - (100 if resume and w < h else 0)
    y = top + (bottom - top - total) / 2
    for kind, height, gap in rows:
        mid = y + height / 2
        if kind == "spin":
            _spin(img, pad, y, 84, 9)
        elif kind == "kicker":
            _text(d, (pad, mid), kicker.upper(), 27, MUTED, "mono", "lm", track=0.12)
        elif kind == "title":
            _imp(d, pad, y, title, size, fit=w - 2 * pad)
        elif kind == "text":
            _text(d, (pad, mid), text_lines.pop(0), 45, ERROR if error else SOFT, "sans", "lm")
        else:
            _text(d, (pad, mid), note_lines.pop(0), 30, MUTED, "sans", "lm")
        y += height + gap
    return img


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


def _code(img, d, x, y, code, box_w, box_h, size, gap, dash_w):
    """The pairing code in boxes (canvas `.code`): one box per character, the dash between them."""
    for ch in code:
        if ch == "-":
            _text(d, (x + dash_w / 2, y + box_h / 2), ch, round(size * 0.83), DASH, "mono", "mm")
            x += dash_w + gap
            continue
        _sprite(img, x, y, (box_w, box_h), lambda dr, k: (
            dr.rounded_rectangle([0, 0, box_w * k - 1, box_h * k - 1], radius=box_w * 0.19 * k, fill=LINE2),
            dr.rounded_rectangle([2 * k, 2 * k, (box_w - 2) * k - 1, (box_h - 2) * k - 1],
                                 radius=(box_w * 0.19 - 2) * k, fill=SURFACE)))
        _text(d, (x + box_w / 2, y + box_h / 2), ch, size, FG, "mono", "mm")
        x += box_w + gap


def _code_width(code, box_w, gap, dash_w):
    return sum(dash_w if ch == "-" else box_w for ch in code) + gap * (len(code) - 1)


def pairing(path, code=None, pair_url=None, expires_at=None, footer="", logo=None, rotation=0):
    """Steps, the code in boxes and a QR to pair_url (canvas "Tv - koppelen", staand: "Tv - koppelen
    staand"). code None -> 'connecting' (canvas "Tv - verbinden")."""
    from PIL import ImageDraw
    if not code:
        img = _notice(_canvas(portrait(rotation)), footer, logo, "Verbinden", text="Even geduld",
                      note="Zo verschijnt hier een koppelcode.", busy=True, title_size=144)
        return _save(img, path, rotation)
    host = urlparse(pair_url).netloc if pair_url else ""
    steps = (f"Ga naar {host}/schermen" if host else "Ga in het beheer naar Schermen", "Vul deze code in")
    until = time.strftime("%H:%M", time.localtime(expires_at)) if expires_at else None
    expiry = f"Geldig tot {until}, daarna verschijnt vanzelf een nieuwe code" if until else None
    title = "Koppel dit scherm".rsplit(" ", 1)  # two lines, like the canvas
    if portrait(rotation):
        img = _canvas(True)
        d = ImageDraw.Draw(img, "RGBA")
        pad, w = 80, H
        _brand(img, d, logo, pad, 96, 104, 32, stacked=True)
        y = 96 + 84 + 56
        for line in title:
            _imp(d, pad, y, line, 136, fit=w - 2 * pad)
            y += round(0.95 * 136)
        y += 56
        for i, s in enumerate(steps):
            _step(d, pad, y + 36, i + 1, s, 36, fit=w - 2 * pad - 48)
            y += 50 + 24
        y += 32
        cw = _code_width(code, 116, 12, 32)
        scale = min(1.0, (w - 2 * pad) / cw)
        _code(img, d, pad, y, code, round(116 * scale), round(152 * scale), round(76 * scale), 12, round(32 * scale))
        y += round(152 * scale) + 28
        if expiry:
            _text(d, (pad, y), expiry, 28, MUTED, "sans", "lt", fit=w - 2 * pad)
        foot = W - 64 - 24 * 0.7 - 12
        if pair_url:
            q = 520
            qy = foot - 48 - 32 - 24 - q
            _qr(img, pair_url, (w - q) // 2, qy, q)
            _text(d, (w // 2, qy + q + 24), "Of scan met je telefoon", 32, MUTED, "sans", "mt")
        _foot(img, d, "VERBONDEN", footer, size=24, pad=pad, bottom=64, dot=TONES["ok"][0])
        return _save(img, path, rotation)
    img = _canvas()
    d = ImageDraw.Draw(img, "RGBA")
    _brand(img, d, logo, 120, 96, 105, 39)
    q = 420
    qx = W - 120 - q
    col = qx - 108 - 120
    total = 2 * round(0.95 * 114) + 42 + 2 * 46 + 21 + 54 + 138 + (33 + 30 if expiry else 0)
    top, bottom = 96 + 84 + 40, H - 72 - 40 - 40
    y = top + (bottom - top - total) / 2
    for line in title:
        _imp(d, 120, y, line, 114, fit=col)
        y += round(0.95 * 114)
    y += 42
    for i, s in enumerate(steps):
        _step(d, 120, y + 36, i + 1, s, fit=col - 44)
        y += 46 + 21
    y += 54 - 21
    _code(img, d, 120, y, code, 108, 138, 72, 15, 36)
    y += 138 + 33
    if expiry:
        _text(d, (120, y), expiry, 27, MUTED, "sans", "lt", fit=col)
    if pair_url:
        qy = top + (bottom - top - (q + 21 + 30)) / 2
        _qr(img, pair_url, qx, int(qy), q)
        _text(d, (qx + q / 2, qy + q + 21), "Of scan met je telefoon", 27, MUTED, "sans", "mt")
    _foot(img, d, "VERBONDEN", footer, dot=TONES["ok"][0])
    return _save(img, path, rotation)


def waiting(path, name=None, footer="", logo=None, rotation=0):
    """Paired, nothing to play yet (canvas "Tv - rustscherm")."""
    img = _notice(_canvas(portrait(rotation)), footer, logo, name or "Gekoppeld",
                  kicker="Gekoppeld als" if name else None, text="Wacht op een playlist",
                  note="Kies in het beheer onder Schermen welke playlist hier draait en zorg dat hij "
                       "gepubliceerd is.")
    return _save(img, path, rotation)


def identify(path, name, footer="", logo=None, rotation=0):
    """"Scherm identificeren": the screen name, huge, so you can see which screen is which (canvas
    "Tv - identificeren", without the content behind it: the PNG replaces the video)."""
    from PIL import ImageDraw
    img = _canvas(portrait(rotation), backdrop=False)
    d = ImageDraw.Draw(img)
    w, h = img.size
    cx, size = w // 2, int(min(300, 0.16 * w))
    _sprite(img, 36, 36, (w - 72, h - 72), lambda dr, k: dr.rounded_rectangle(
        [0, 0, (w - 72) * k - 1, (h - 72) * k - 1], radius=42 * k, outline=MAGENTA, width=9 * k), k=2)
    label, ls = "DIT SCHERM", 33
    tw = d.textlength(label, font=_font(ls, "mono")) + 0.12 * ls * (len(label) - 1)
    pill_w, pill_h, dot = round(39 + 21 + 21 + tw + 39), 84, 21
    top = h // 2 - (pill_h + 42 + round(0.95 * size)) // 2

    def pill(dr, k):
        dr.rounded_rectangle([0, 0, pill_w * k - 1, pill_h * k - 1], radius=pill_h * k / 2, fill=MAGENTA)
        dr.ellipse([39 * k, (pill_h - dot) * k / 2, (39 + dot) * k, (pill_h + dot) * k / 2], fill=FG)
    _sprite(img, cx - pill_w / 2, top, (pill_w, pill_h), pill)
    _text(d, (cx - pill_w / 2 + 39 + dot + 21, top + pill_h / 2), label, ls, FG, "mono", "lm", track=0.12)
    _imp(d, cx, top + pill_h + 42, name, size, fit=w - 200, anchor="m")
    if footer:
        _text(d, (cx, h - 84), footer.upper(), 21, FAINT, "mono", "ms", fit=w - 200, track=0.08)
    return _save(img, path, rotation)


UPDATE_TEXT = "Aan het updaten..."
UPDATE_SIZE = 30
UPDATE_PAD = (24, 18, 30)  # left, top/bottom, right (canvas padding 12px 20px 12px 16px x 1.5)
UPDATE_SPIN, UPDATE_RING = 30, 5


def update_label(path, rotation=0):
    """The small "Aan het updaten..." label (canvas "Tv - aan het updaten": ink box, magenta spinner,
    GT Walsheim Medium) that gst.Display puts bottom-left on its own plane over the playing video
    during a player update. Transparent around a rounded box, alpha PREMULTIPLIED (the vc4 plane
    default blend mode), turned like the screens for a panel at 90/270 (the plane itself cannot
    turn 90)."""
    from PIL import Image, ImageChops, ImageDraw
    font = _font(UPDATE_SIZE, "medium")
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), UPDATE_TEXT, font=font, anchor="lm")
    pl, py, pr = UPDATE_PAD
    gap = 18
    inner = max(UPDATE_SPIN, bottom - top)
    w, h = pl + UPDATE_SPIN + gap + (right - left) + pr, inner + 2 * py

    def box(d, k):
        d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=21 * k, fill=INK + (224,))
        s, r = UPDATE_SPIN * k, UPDATE_RING * k
        sx, sy = pl * k, (h * k - s) / 2
        arc = [sx + r / 2, sy + r / 2, sx + s - r / 2, sy + s - r / 2]
        d.arc(arc, 0, 360, fill=LINE2 + (255,), width=round(r))
        d.arc(arc, 225, 315, fill=MAGENTA + (255,), width=round(r))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    _sprite(img, 0, 0, (w, h), box)
    ImageDraw.Draw(img).text((pl + UPDATE_SPIN + gap - left, h / 2), UPDATE_TEXT, font=font, fill=FG + (255,),
                             anchor="lm")
    r, g, b, a = img.split()
    img = Image.merge("RGBA", [ImageChops.multiply(c, a) for c in (r, g, b)] + [a])
    if portrait(rotation):
        img = img.rotate(rotation, expand=True)  # counter-clockwise, like _turn
    tmp = path + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, path)
    return path


def wifi_badge(status):
    """Top-right badge of the wifi-setup screen: what the Pi is doing now, warn when nothing."""
    if not status:
        return "GEEN VERBINDING", True
    return ("VERBINDEN" if status.startswith("Verbind") else "NETWERK ZOEKEN"), False


def _badge(status):
    label, warn = wifi_badge(status)
    return label, "warn" if warn else "busy"


def _cards(img, d, x, y, items, fit):
    """Kicker + mono value cards side by side (canvas TvWifi: network and password). Returns the height."""
    h = 21 + 18 + 12 + 36 + 21
    for kicker, value in items:
        vw = d.textlength(value, font=_font(36, "mono"))
        kw = d.textlength(kicker, font=_font(18, "mono")) + 0.12 * 18 * (len(kicker) - 1)
        cw = min(round(max(vw, kw)) + 54, fit)

        def paint(dr, k, cw=cw):
            dr.rounded_rectangle([0, 0, cw * k - 1, h * k - 1], radius=30 * k, fill=LINE)
            dr.rounded_rectangle([2 * k, 2 * k, (cw - 2) * k - 1, (h - 2) * k - 1], radius=28 * k, fill=SURFACE)
        _sprite(img, x, y, (cw, h), paint)
        _text(d, (x + 27, y + 21), kicker.upper(), 18, MUTED, "mono", "lt", track=0.12)
        _text(d, (x + 27, y + 21 + 18 + 12), value, 36, FG, "mono", "lt", fit=cw - 54)
        x += cw + 21
        fit -= cw + 21
    return h


def wifi_setup(path, ssid=None, password=None, status=None, error=None, footer="", logo=None,
               portal_url="http://10.42.0.1", rotation=0, setup_url=None, resume=None, ap=None):
    """Wifi setup (canvas "Tv - wifi instellen"). With setup_url: a QR to the App Clip (Bluetooth)
    and the hotspot SSID + password as the route for other phones. Without: a join-QR for the
    hotspot plus a second QR (and the address) to the setup page for phones that don't open it by
    themselves (Private DNS, mobile data). Nothing to offer (no hotspot, no Bluetooth) or busy
    without a hotspot: a notice with the status/error, or 'connect ethernet' (canvas "Tv - geen
    netwerk"). resume: minutes until a paired Pi plays its content again (boot countdown). ap: the
    hotspot is up (default: an ssid was given). While it is briefly down (trying the saved networks,
    connecting) the layout stays: the badge says what happens and the hotspot part dims."""
    from PIL import ImageDraw
    from .netman import wifi_qr
    note = f"Over {resume} min speelt dit scherm verder met de laatste versie" if resume else None
    ap = bool(ssid) if ap is None else ap
    if not setup_url and not (ssid and (ap or status)):
        img = _notice(_canvas(portrait(rotation)), footer, logo, status or "Geen netwerk", kicker="Wifi",
                      text=error or ("Het instelnetwerk komt zo terug" if status
                                     else "Sluit een netwerkkabel aan, of wacht op de wifi-instelling"),
                      busy=bool(status) and not error, badge=_badge(status), error=bool(error),
                      resume=note, title_size=168)
        return _save(img, path, rotation)
    tall = portrait(rotation)
    img = _canvas(tall)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    pad = 80 if tall else 120
    if tall:  # like "Koppel dit scherm" staand: stacked wordmark, the QR codes under the text
        _brand(img, d, logo, pad, 84, 104, 32, stacked=True)
    else:
        _brand(img, d, logo, pad, 84, 93, 33)
    _chip(img, d, w - pad, 84 + 37, *_badge(status))
    _foot(img, d, None, footer, resume=note, pad=pad, bottom=66)
    top, bottom = 84 + 75 + 42, h - 66 - 60 - 30 - (100 if tall and note else 0)
    ind = pad + 44  # text under a step lines up with the step's text
    title = 136 if tall else 126
    rows = []  # (height, gap after, draw(y))
    add = rows.append
    if setup_url:  # App Clip QR; the hotspot (if up) as the route for other phones
        q = 520 if tall else 450
        qx = (w - q) // 2 if tall else w - pad - q
        fit = w - ind - pad if tall else qx - 96 - pad - 60
        add((round(0.95 * title), 36, lambda y: _imp(d, pad, y, "Wifi instellen", title)))
        add((46, 9, lambda y: _step(d, pad, y + 36, 1, "Scan de QR-code met de camera van je iPhone", fit=fit + 16)))
        add((36, 30, lambda y: _text(d, (ind, y + 18), "‘Scherm instellen’ opent en zet de wifi van het scherm "
                                     "voor je in", 27, MUTED, "sans", "lm", fit=fit)))
        if ssid and not ap:
            add((46, 30, lambda y: _step(d, pad, y + 36, 2, "Andere telefoon? Het instelnetwerk komt zo terug",
                                         fit=fit + 16)))
        elif ssid:
            add((46, 21, lambda y: _step(d, pad, y + 36, 2, "Andere telefoon? Verbind met het wifi-netwerk:",
                                         fit=fit + 16)))
            add((108, 21, lambda y: _cards(img, d, ind, y, (("Netwerk", ssid), ("Wachtwoord", password)), fit)))
            add((30, 30, lambda y: _text(d, (ind, y + 15), f"Geen pagina? Open {portal_url} in je browser", 24,
                                         MUTED, "mono", "lm", fit=fit)))
        if error or status:
            add((42, 0, lambda y: _text(d, (ind, y + 21), error or status, 33, ERROR if error else TONES["busy"][0],
                                        "medium", "lm", fit=fit)))
        total = sum(r[0] + r[1] for r in rows) - rows[-1][1]
        qr_h = q + 21 + 30
        if tall:  # text and QR as one column, centred
            y = top + (bottom - top - (total + 72 + qr_h)) / 2
            qy = y + total + 72
        else:
            y = top + (bottom - top - total) / 2
            qy = top + (bottom - top - qr_h) / 2
        for height, gap, draw in rows:
            draw(y)
            y += height + gap
        _qr(img, setup_url, qx, int(qy), q)
        _text(d, (qx + q / 2, qy + q + 21), "Scan met je camera", 27, MUTED, "sans", "mt")
        return _save(img, path, rotation)
    # Two QR cards, 1 = join the hotspot, 2 = open the page: stacked on the right, or side by side
    # under the text on a portrait panel.
    q = 400 if tall else 300
    qx = w - pad - q
    fit = w - ind - pad if tall else qx - 96 - pad - 60
    add((round(0.95 * title), 36, lambda y: _imp(d, pad, y, "Wifi instellen", title)))
    add((46, 21, lambda y: _step(d, pad, y + 36, 1, "Scan code 1, of maak verbinding met:", fit=fit + 16)))
    add((108, 18, lambda y: _cards(img, d, ind, y, (("Netwerk", ssid), ("Wachtwoord", password)), fit)))
    add((30, 36, lambda y: _text(d, (ind, y + 15), "De instelpagina opent daarna vanzelf op je telefoon", 27, MUTED,
                                 "sans", "lm", fit=fit)))
    add((46, 15, lambda y: _step(d, pad, y + 36, 2, "Opent er niets? Scan code 2, of ga naar:", fit=fit + 16)))
    add((48, 24, lambda y: _text(d, (ind, y + 24), portal_url, 42, FG, "mono", "lm", fit=fit)))
    if error or status:
        add((42, 12, lambda y: _text(d, (ind, y + 21), error or status, 33, ERROR if error else TONES["busy"][0],
                                     "medium", "lm", fit=fit)))
    if not ap:
        add((36, 0, lambda y: _text(d, (ind, y + 18), "Het instelnetwerk komt zo terug", 27, MUTED, "sans", "lm",
                                    fit=fit)))
    total = sum(r[0] + r[1] for r in rows) - rows[-1][1]
    card = q + 14 + 30
    if tall:
        gap = w - 2 * pad - 2 * q
        y = top + (bottom - top - (total + 72 + card)) / 2
        spots = [(pad, y + total + 72), (pad + q + gap, y + total + 72)]
    else:
        y = top + (bottom - top - total) / 2
        qy0 = top + (bottom - top - (2 * card + 30)) / 2
        spots = [(qx, qy0), (qx, qy0 + card + 30)]
    for height, gap_after, draw in rows:
        draw(y)
        y += height + gap_after
    for (sx, sy), (data, label) in zip(spots, ((wifi_qr(ssid, password), "1  Verbind met de wifi"),
                                               (portal_url, "2  Open de instelpagina"))):
        sx, sy = int(sx), int(sy)
        _qr(img, data, sx, sy, q)
        n, s = label.split("  ", 1)
        nw = d.textlength(f"0{n}", font=_font(20, "mono"))
        sw = d.textlength(s, font=_font(27))
        lx = sx + (q - nw - 12 - sw) / 2
        _text(d, (lx, sy + q + 14 + 27), f"0{n}", 20, MAGENTA, "mono", "ls")
        _text(d, (lx + nw + 12, sy + q + 14 + 27), s, 27, MUTED, "sans", "ls")
        if not ap:  # hotspot briefly down: the codes don't work right now
            d.rounded_rectangle([sx, sy, sx + q, sy + q], radius=36, fill=INK + (190,))
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
    assert wifi_badge(None) == ("GEEN VERBINDING", True)
    assert wifi_badge("Opgeslagen netwerken proberen…") == ("NETWERK ZOEKEN", False)
    assert wifi_badge("Verbinden met Kantine…") == ("VERBINDEN", False)
    assert os.path.exists(ZZ_LOGO), ZZ_LOGO
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:  # update label: upright for the viewer, premultiplied
        flat = Image.open(update_label(os.path.join(tmp, "u0.png"))).convert("RGBA")
        assert flat.width > 4 * flat.height and flat.height < 80, flat.size
        assert flat.getpixel((0, 0))[3] == 0  # rounded corner: transparent, so the video shows
        px = flat.tobytes()
        assert all(px[i + c] <= px[i + 3] for i in range(0, len(px), 4) for c in range(3))  # premultiplied
        # the spinner's magenta top arc on the left, the text to its right
        dot = (UPDATE_PAD[0] + UPDATE_SPIN // 2, (flat.height - UPDATE_SPIN) // 2 + UPDATE_RING // 2)
        px = flat.getpixel(dot)
        assert px[0] > 100 and px[1] < 30 and px[3] > 200, px  # magenta (premultiplied, antialiased)
        for rot, at in ((90, lambda i: (dot[1], i.height - 1 - dot[0])), (270, lambda i: (i.width - 1 - dot[1], dot[0]))):
            turned = Image.open(update_label(os.path.join(tmp, f"u{rot}.png"), rot)).convert("RGBA")
            assert turned.size == (flat.height, flat.width), (rot, turned.size)
            # the label's start (spinner) is at the viewer's left: the panel's bottom (90) / top (270)
            assert turned.getpixel(at(turned)) == px, (rot, turned.getpixel(at(turned)))
    pts = _outline(BLOB)  # the logo path parses into the 57x48 viewBox, closed
    assert all(-1 < x < 58 and -1 < y < 49 for x, y in pts) and abs(pts[0][0] - pts[-1][0]) < 0.1, pts[:2]
    print("screens self-check OK")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["check"]:
        _selfcheck()
        sys.exit()
    if sys.argv[1:] == ["logo"]:
        print(logo_png())
        sys.exit()
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    f = "pi-a1b2c3d4e5f6a7b8 · 0.1.0"
    url = "https://app.zekerzichtbaar.tv/schermen?code=K7Q-4M2"
    print(pairing(os.path.join(out, "pairing.png"), "K7Q-4M2", url, time.time() + 600, f))
    print(pairing(os.path.join(out, "pairing-connecting.png"), footer=f))
    print(waiting(os.path.join(out, "waiting.png"), "Kantine links", f))
    print(waiting(os.path.join(out, "waiting-noname.png"), None, f))
    print(identify(os.path.join(out, "identify.png"), "Kantine links", f))
    for r in (0, 90, 270):
        print(update_label(os.path.join(out, f"update-label-{r}.png"), r))
    clip = "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw"
    print(wifi_setup(os.path.join(out, "wifi-clip.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f, setup_url=clip))
    print(wifi_setup(os.path.join(out, "wifi-clip-resume.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                     setup_url=clip, resume=9))
    print(wifi_setup(os.path.join(out, "wifi-resume.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f, resume=1))
    print(wifi_setup(os.path.join(out, "wifi-none-resume.png"), footer=f, resume=10))
    print(wifi_setup(os.path.join(out, "wifi-clip-error.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                     setup_url=clip, error="Verbinden met Kantine mislukt: Verkeerd wachtwoord?"))
    print(wifi_setup(os.path.join(out, "wifi-clip-noap.png"), footer=f, setup_url=clip,
                     error="Het instelnetwerk start niet. Sluit een netwerkkabel aan."))
    print(wifi_setup(os.path.join(out, "wifi.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f))
    print(wifi_setup(os.path.join(out, "wifi-retry.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f, ap=False,
                     status="Opgeslagen netwerken proberen…", resume=1))
    print(wifi_setup(os.path.join(out, "wifi-clip-retry.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f, ap=False,
                     setup_url=clip, status="Opgeslagen netwerken proberen…", resume=2))
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
        print(pairing(os.path.join(out, f"pairing-connecting-{r}.png"), footer=f, rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-clip-{r}.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                         setup_url=clip, rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-busy-{r}.png"), status="Verbinden met Kantine…", footer=f,
                         rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-clip-error-{r}.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                         setup_url=clip, error="Verbinden met Kantine mislukt: Verkeerd wachtwoord?", rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-clip-retry-{r}.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                         ap=False, setup_url=clip, status="Opgeslagen netwerken proberen…", resume=2, rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-clip-resume-{r}.png"), "Narrowcast-1E2F", "k7q4m2xa", footer=f,
                         setup_url=clip, resume=9, rotation=r))
        print(wifi_setup(os.path.join(out, f"wifi-clip-noap-{r}.png"), footer=f, setup_url=clip,
                         error="Het instelnetwerk start niet. Sluit een netwerkkabel aan.", rotation=r))
