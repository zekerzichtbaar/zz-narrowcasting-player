"""Spike (not part of the agent): can a small PNG sit on a second DRM/KMS plane above the
zero-copy video, without extra dropped frames?

Plays a render exactly like the agent (pi_player.gst.Display.play: v4l2h264dec -> kmssink, looping
on the group clock), then runs three phases of --seconds each and prints the kmssink frame counters
per phase:

    A  video only
    B  video + label "Aan het updaten..." on its own overlay plane (second kmssink, same DRM fd)
    C  video only again (label pipeline stopped: the label must disappear)

A DRM device has one master, and only the master may set planes. The second kmssink therefore gets
the DRM fd of the first one (kmssink `fd` property, GStreamer >= 1.22), selectable with --fd:

    video  read the fd from the video kmssink after it started (default)
    own    open the vc4 card here, give the same fd to both sinks (this process stays master)
    none   the label sink opens its own fd (expected to fail with "Permission denied": proves the
           master problem, nothing else)

Run on the Pi with the agent stopped (see docs/slides/update-overlay/SPIKE.md in the CMS repo):

    sudo systemctl stop pi-player
    sudo PYTHONPATH=/opt/pi-player python3 /tmp/overlay_spike.py --video /var/lib/pi-player/current.mp4
    sudo systemctl start pi-player

Without GStreamer (dev machine): `python3 tools/overlay_spike.py --selfcheck` checks the corner
math and the fallback PNG only.
"""
import argparse
import os
import struct
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # repo checkout: tools/ next to pi_player/
sys.path.append("/opt/pi-player")  # on the Pi: the installed agent

TEXT = "Aan het updaten..."
BG, FG = (7, 8, 10), (242, 244, 246)  # screens.py BG / FG


def label_rect(rotation, disp_w, disp_h, w, h, margin):
    """Where an upright w x h label goes on the panel (kmssink coordinates, x, y, width, height) so
    it sits bottom-left for the viewer. The panel hangs `rotation` degrees clockwise and the picture
    is turned back counter-clockwise (screens._turn), so the viewer's bottom-left corner is the
    panel's bottom-left (0), bottom-right (90) or top-left (270), and the label is w x h turned."""
    if rotation == 90:
        return disp_w - margin - h, disp_h - margin - w, h, w
    if rotation == 270:
        return margin, margin, h, w
    return margin, disp_h - margin - h, w, h


def _png(width, height, rgba):
    """Minimal RGBA PNG of one colour (no Pillow needed)."""
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    rows = b"".join(b"\0" + bytes(rgba) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def make_label(path, size, opacity, rotation):
    """The label as a premultiplied-alpha PNG (vc4 planes blend "Pre-multiplied" by default), turned
    for the panel. Pillow (on every Pi) draws text and rounded corners; without it a plain box."""
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        w, h = int(size * 10), int(size * 1.9)
        a = round(255 * opacity)
        with open(path, "wb") as f:  # premultiplied: rgb * alpha
            f.write(_png(*((w, h) if rotation not in (90, 270) else (h, w)),
                         tuple(round(c * a / 255) for c in BG) + (a,)))
        return w, h
    try:
        from pi_player.screens import _font
        font = _font(size, "semi")
    except Exception:  # noqa: BLE001 - spike: any font will do
        from PIL import ImageFont
        font = ImageFont.load_default()
    pad_x, pad_y = round(size * 0.75), round(size * 0.45)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, t, r, b = probe.textbbox((0, 0), TEXT, font=font)
    w, h = (r - x0) + 2 * pad_x, (b - t) + 2 * pad_y
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=round(h * 0.3), fill=BG + (round(255 * opacity),))
    d.text((pad_x - x0, pad_y - t), TEXT, font=font, fill=FG + (255,))
    rch, gch, bch, ach = img.split()
    img = Image.merge("RGBA", [ImageChops.multiply(c, ach) for c in (rch, gch, bch)] + [ach])
    if rotation in (90, 270):
        img = img.rotate(rotation, expand=True)  # counter-clockwise, like screens._turn
    img.save(path)
    return w, h


def vc4_card():
    """/dev/dri/cardN of the display controller (vc4), not the 3D one (v3d)."""
    for name in sorted(os.listdir("/sys/class/drm")):
        if not name.startswith("card") or "-" in name:
            continue
        try:
            drv = os.path.basename(os.readlink(f"/sys/class/drm/{name}/device/driver"))
        except OSError:
            continue
        if "vc4" in drv:
            return f"/dev/dri/{name}"
    raise SystemExit("no vc4 card in /sys/class/drm (use --card)")


ARGB8888 = 0x34325241  # DRM fourcc 'AR24' = GStreamer BGRA


def overlay_planes(fd):
    """[(plane_id, crtc_id, possible_crtcs, has_argb)] via libdrm (ctypes). Without the universal
    planes client cap (kmssink does not set it unless it modesets) only overlay planes are listed."""
    import ctypes
    import ctypes.util
    u32, pu32 = ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)

    class PlaneRes(ctypes.Structure):
        _fields_ = [("count_planes", u32), ("planes", pu32)]

    class Plane(ctypes.Structure):
        _fields_ = [("count_formats", u32), ("formats", pu32), ("plane_id", u32), ("crtc_id", u32),
                    ("fb_id", u32), ("crtc_x", u32), ("crtc_y", u32), ("x", u32), ("y", u32),
                    ("possible_crtcs", u32), ("gamma_size", u32)]

    drm = ctypes.CDLL(ctypes.util.find_library("drm") or "libdrm.so.2")
    drm.drmModeGetPlaneResources.restype = ctypes.POINTER(PlaneRes)
    drm.drmModeGetPlane.restype = ctypes.POINTER(Plane)
    drm.drmModeGetPlane.argtypes = [ctypes.c_int, u32]
    res = drm.drmModeGetPlaneResources(fd)
    if not res:
        raise OSError(f"drmModeGetPlaneResources failed on fd {fd}")
    out = []
    for i in range(res.contents.count_planes):
        p = drm.drmModeGetPlane(fd, res.contents.planes[i])
        if p:
            c = p.contents
            fmts = {c.formats[j] for j in range(c.count_formats)}
            out.append((c.plane_id, c.crtc_id, c.possible_crtcs, ARGB8888 in fmts))
            drm.drmModeFreePlane(p)
    drm.drmModeFreePlaneResources(res)
    return out


def pick_plane(planes, video_plane):
    """A free overlay plane after the video's (vc4: higher plane id = higher default zpos), on the
    same CRTCs, that takes ARGB8888. None if there is none."""
    video = next((p for p in planes if p[0] == video_plane), None)
    for pid, crtc, possible, argb in planes:
        if pid > video_plane and not crtc and argb and (video is None or possible & video[2]):
            return pid
    return None


def counters(display):
    s = display.stats()
    return int(s.get("rendered", 0)), int(s.get("dropped", 0))


def phase(name, display, seconds):
    r0, d0 = counters(display)
    t0, drifts = time.monotonic(), []
    while time.monotonic() - t0 < seconds:
        if not display.alive():
            raise SystemExit(f"video pipeline died in phase {name}: {display.error}")
        d = display.drift()
        if d is not None:
            drifts.append(abs(d) * 1000)
        time.sleep(1)
    r1, d1 = counters(display)
    secs = time.monotonic() - t0
    worst = max(drifts) if drifts else float("nan")
    line = (f"PHASE {name}: rendered +{r1 - r0} ({(r1 - r0) / secs:.2f} fps), dropped +{d1 - d0}, "
            f"worst drift {worst:.1f} ms over {secs:.0f} s")
    print(line, flush=True)
    return line


def label_pipeline(Gst, png, video_sink, args, fd, plane, rect):
    x, y, w, h = rect
    props = f"plane-id={plane} " if plane is not None else ""
    p = Gst.parse_launch(
        "appsrc name=src is-live=true do-timestamp=true format=time caps=image/png ! pngdec ! "
        "videoconvert ! video/x-raw,format=BGRA ! "
        f'{video_sink} name=osd sync=false skip-vsync=true {props}render-rectangle="<{x},{y},{w},{h}>"')
    osd = p.get_by_name("osd")
    if fd >= 0:
        osd.set_property("fd", fd)  # set before every start: kmssink forgets an external fd on stop
    if args.zpos >= 0:
        osd.set_property("plane-properties", Gst.Structure.new_from_string(f"props,zpos=(int){args.zpos}"))
    if p.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        raise RuntimeError("label pipeline cannot start")
    with open(png, "rb") as f:
        r = p.get_by_name("src").emit("push-buffer", Gst.Buffer.new_wrapped(f.read()))
    if r != Gst.FlowReturn.OK:
        raise RuntimeError(f"label not accepted ({r.value_nick})")
    deadline = time.monotonic() + 5  # an error (e.g. drmModeSetPlane: Permission denied) shows up here
    while time.monotonic() < deadline:
        m = p.get_bus().pop_filtered(Gst.MessageType.ERROR)
        if m is not None:
            err, dbg = m.parse_error()
            p.set_state(Gst.State.NULL)
            raise RuntimeError(f"{err.message} ({dbg})")
        time.sleep(0.1)
    print(f"label: plane-id {osd.get_property('plane-id')}, fd {fd}, rect {rect}", flush=True)
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", default="/var/lib/pi-player/current.mp4")
    ap.add_argument("--sink", default="kmssink", help='video sink as in config.toml (e.g. "kmssink connector-id=32")')
    ap.add_argument("--fd", choices=("video", "own", "none"), default="video")
    ap.add_argument("--card", help="DRM card for --fd own (default: the vc4 one)")
    ap.add_argument("--plane-id", type=int, default=-1, help="overlay plane for the label (default: the first free one after the video's)")
    ap.add_argument("--zpos", type=int, default=-1, help="set the label plane's zpos (vc4: 1..17)")
    ap.add_argument("--seconds", type=int, default=60, help="length of each phase")
    ap.add_argument("--rotation", type=int, choices=(0, 90, 270), default=0)
    ap.add_argument("--size", type=int, default=32, help="label text size in px (~3 %% of 1080)")
    ap.add_argument("--margin", type=int, default=54)
    ap.add_argument("--opacity", type=float, default=0.88, help="label background opacity")
    ap.add_argument("--x", type=int, help="override the label position (panel px)")
    ap.add_argument("--y", type=int)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        return _selfcheck()

    from pi_player import gst
    from pi_player.gst import Display
    try:
        Gst = gst._gst()
    except (ImportError, ValueError) as e:
        raise SystemExit(f"no GStreamer here ({e}): run this on the Pi, or use --selfcheck")
    print(f"GStreamer {Gst.version_string()}", flush=True)
    sink, own = args.sink, -1
    if args.fd == "own":
        own = os.open(args.card or vc4_card(), os.O_RDWR | os.O_CLOEXEC)
        sink = f"{sink} fd={own}"
        print(f"own DRM fd {own} on {args.card or vc4_card()}", flush=True)
    display = Display(sink)
    duration = display.play(args.video, time.time())
    vsink = display.pipe.get_by_name("vsink")
    dw, dh = vsink.get_property("display-width"), vsink.get_property("display-height")
    print(f"video: {args.video} ({duration:.3f} s), display {dw}x{dh}, "
          f"plane-id {vsink.get_property('plane-id')}, fd {vsink.get_property('fd')}", flush=True)
    results = [phase("A (video only)", display, args.seconds)]

    tmp = tempfile.mkdtemp()
    png = os.path.join(tmp, "label.png")
    w, h = make_label(png, args.size, args.opacity, args.rotation)
    rect = label_rect(args.rotation, dw, dh, w, h, args.margin)
    if args.x is not None:
        rect = (args.x, rect[1], rect[2], rect[3])
    if args.y is not None:
        rect = (rect[0], args.y, rect[2], rect[3])
    vfd, vplane = vsink.get_property("fd"), vsink.get_property("plane-id")
    plane = args.plane_id if args.plane_id >= 0 else None
    try:
        planes = overlay_planes(vfd)
        for pid, crtc, possible, argb in planes:
            print(f"  plane {pid}: crtc {crtc}, possible_crtcs {possible:#x}, ARGB8888 {argb}"
                  f"{'  <- video' if pid == vplane else ''}", flush=True)
        if plane is None:
            plane = pick_plane(planes, vplane)
    except OSError as e:
        print(f"cannot list planes ({e}): kmssink picks (and probably takes the video's plane)", flush=True)
    fd = {"video": vfd, "own": own, "none": -1}[args.fd]
    try:
        osd = label_pipeline(Gst, png, args.sink, args, fd, plane, rect)
    except RuntimeError as e:
        print(f"LABEL FAILED: {e}", flush=True)
        results.append(phase("B (label failed, video only)", display, args.seconds))
    else:
        results.append(phase("B (video + label)", display, args.seconds))
        osd.set_state(Gst.State.NULL)
        print("label pipeline stopped: the label should be gone now", flush=True)
    results.append(phase("C (video only again)", display, args.seconds))
    display.close()
    if own >= 0:
        os.close(own)
    print("\n--- copy this back ---")
    print(f"GStreamer {Gst.version_string()}, --fd {args.fd}, video plane {vplane}, label plane {plane}, zpos {args.zpos}, "
          f"rotation {args.rotation}, display {dw}x{dh}, label rect {rect}")
    for line in results:
        print(line)


def _selfcheck():
    # 1920x1080 panel, 300x50 label, margin 54: bottom-left for the viewer in every rotation
    assert label_rect(0, 1920, 1080, 300, 50, 54) == (54, 1080 - 54 - 50, 300, 50)
    # hung 90 clockwise: viewer's bottom-left = panel's bottom-right, label turned (50 x 300)
    assert label_rect(90, 1920, 1080, 300, 50, 54) == (1920 - 54 - 50, 1080 - 54 - 300, 50, 300)
    # hung 270 clockwise: viewer's bottom-left = panel's top-left
    assert label_rect(270, 1920, 1080, 300, 50, 54) == (54, 54, 50, 300)
    for r in (0, 90, 270):  # always fully on the panel
        x, y, w, h = label_rect(r, 1920, 1080, 300, 50, 54)
        assert 0 <= x and 0 <= y and x + w <= 1920 and y + h <= 1080, r
    # label plane: after the video's, free, ARGB, same CRTCs
    planes = [(40, 0, 0x3, True), (52, 91, 0x3, True), (60, 0, 0x3, False), (70, 0, 0x4, True), (80, 0, 0x3, True)]
    assert pick_plane(planes, 52) == 80
    assert pick_plane(planes, 80) is None
    data = _png(3, 2, (1, 2, 3, 4))
    assert data.startswith(b"\x89PNG") and struct.unpack(">II", data[16:24]) == (3, 2)
    with tempfile.TemporaryDirectory() as tmp:
        for r in (0, 90):
            path = os.path.join(tmp, f"l{r}.png")
            w, h = make_label(path, 32, 0.88, r)
            with open(path, "rb") as f:
                pw, ph = struct.unpack(">II", f.read()[16:24])
            assert (pw, ph) == ((w, h) if r == 0 else (h, w)), (r, pw, ph, w, h)
    print("overlay_spike self-check OK")


if __name__ == "__main__":
    main()
