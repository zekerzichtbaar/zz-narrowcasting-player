"""Everything on HDMI goes through GStreamer: the looping video and the setup-screen PNGs.

One pipeline at a time owns the screen. The screen pipeline stays up from one PNG to the next
(appsrc), so changing screens never leaves HDMI empty; only screen <-> video swaps pipelines, and
quiet_console() makes that moment black instead of the tty1 console.
The one exception: during a player update a small label pipeline ("Aan het updaten...") sits on
its own overlay plane above the video (Display.label), sharing the agent's DRM fd.

The video pipeline runs on the system realtime clock (chrony keeps it equal on every Pi) with a
base time chosen so that media position p plays at epoch + p (see sync.py). It loops with
non-flushing segment seeks: no gap, no stall and no re-sync at the loop boundary.
"""
import ctypes
import ctypes.util
import fcntl
import logging
import os
import re
import struct
import threading
import time

from . import sync

log = logging.getLogger("pi-player")
Gst = None

# The Pi 4 hardware decoder refuses streams without colour tags (common in CMS/ffmpeg exports)
# with "not-negotiated"; tagging them bt709 makes it accept them. Zero-copy into kmssink.
HW_DECODE = "h264parse name=parse ! capssetter caps=video/x-h264,colorimetry=bt709 ! v4l2h264dec"
SW_DECODE = "h264parse name=parse ! avdec_h264 ! videoconvert"  # dev machines / no v4l2 decoder
QUEUE = "queue max-size-time=2000000000 max-size-bytes=0 max-size-buffers=0"

# Rotation (CMS `screens.rotation`) = how the panel hangs, turned clockwise. Only the setup screens
# (PIL, screens.py) turn on the Pi. Video never does: turning on a Pi 4 was too heavy (GPU froze with
# wrong colours, CPU managed 1 fps), so for a panel at 90/270 the CMS serves a variant of the render
# with the picture already turned, a plain 1920x1080 file that plays zero-copy into kmssink.
ROTATIONS = (0, 90, 270)
STILL_SRC = "appsrc name=src is-live=true do-timestamp=true format=time caps=image/png"
KDSETMODE, KD_GRAPHICS = 0x4B3A, 0x01  # linux/kd.h


def quiet_console(tty="/dev/tty1", fb="fb0"):
    """While no pipeline owns HDMI (between two of them, or while the agent restarts) the kernel
    shows its framebuffer console. Graphics mode stops it drawing text there and the wiped
    framebuffer is black, so that moment is black instead of a terminal. Best effort: the VT keeps
    the mode until systemd disallocates it when the service stops."""
    try:
        fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
        try:
            fcntl.ioctl(fd, KDSETMODE, KD_GRAPHICS)
        finally:
            os.close(fd)
        sysfs = f"/sys/class/graphics/{fb}/"
        with open(sysfs + "virtual_size") as f:
            w, h = (int(v) for v in f.read().split(","))
        with open(sysfs + "bits_per_pixel") as f:
            bpp = int(f.read())
        with open(f"/dev/{fb}", "wb", buffering=0) as f:
            f.write(bytes(w * h * bpp // 8))
        log.info("console: tty1 in graphics mode, %s wiped", fb)
    except (OSError, ValueError) as e:
        log.warning("console: cannot blank tty1 (%s)", e)


# ---------- update label on its own DRM plane ----------
# "Aan het updaten..." (screens.update_label) sits on a second overlay plane above the video: the
# HVS blends it, so the video stays zero-copy. Only the DRM master may set planes, so the agent opens
# the vc4 card once (and stays master) and hands that fd to every kmssink (`fd`, GStreamer >= 1.22).
# Findings: CMS repo docs/slides/update-overlay/SPIKE.md. Any failure = a warning, the video plays on.
LABEL_MARGIN = 54  # px from the panel edges (screens._mark margin)
ARGB8888 = 0x34325241  # DRM fourcc 'AR24' = GStreamer BGRA


def vc4_card():
    """/dev/dri/cardN of the display controller (vc4), not the 3D one (v3d); None if there is none."""
    try:
        names = sorted(os.listdir("/sys/class/drm"))
    except OSError:
        return None
    for name in names:
        if not name.startswith("card") or "-" in name:
            continue
        try:
            drv = os.path.basename(os.readlink(f"/sys/class/drm/{name}/device/driver"))
        except OSError:
            continue
        if "vc4" in drv:
            return f"/dev/dri/{name}"
    return None


def overlay_planes(fd):
    """[(plane_id, crtc_id, possible_crtcs, has_argb)] via libdrm (ctypes). Without the universal
    planes client cap (kmssink only sets it when it modesets) these are the overlay planes."""
    u32, pu32 = ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)

    class PlaneRes(ctypes.Structure):
        _fields_ = [("count_planes", u32), ("planes", pu32)]

    class Plane(ctypes.Structure):
        _fields_ = [("count_formats", u32), ("formats", pu32), ("plane_id", u32), ("crtc_id", u32),
                    ("fb_id", u32), ("crtc_x", u32), ("crtc_y", u32), ("x", u32), ("y", u32),
                    ("possible_crtcs", u32), ("gamma_size", u32)]

    drm = ctypes.CDLL(ctypes.util.find_library("drm") or "libdrm.so.2")
    drm.drmModeGetPlaneResources.restype = ctypes.POINTER(PlaneRes)
    drm.drmModeGetPlaneResources.argtypes = [ctypes.c_int]
    drm.drmModeGetPlane.restype = ctypes.POINTER(Plane)
    drm.drmModeGetPlane.argtypes = [ctypes.c_int, u32]
    res = drm.drmModeGetPlaneResources(fd)
    if not res:
        raise OSError(f"drmModeGetPlaneResources failed on fd {fd}")
    out = []
    try:
        for i in range(res.contents.count_planes):
            p = drm.drmModeGetPlane(fd, res.contents.planes[i])
            if p:
                c = p.contents
                fmts = {c.formats[j] for j in range(c.count_formats)}
                out.append((c.plane_id, c.crtc_id, c.possible_crtcs, ARGB8888 in fmts))
                drm.drmModeFreePlane(p)
    finally:
        drm.drmModeFreePlaneResources(res)
    return out


def pick_plane(planes, video_plane):
    """A free overlay plane after the video's (vc4: higher plane id = higher default zpos, so on
    top), on the same CRTCs, that takes ARGB8888. None if there is none."""
    video = next((p for p in planes if p[0] == video_plane), None)
    for pid, crtc, possible, argb in planes:
        if pid > video_plane and not crtc and argb and (video is None or possible & video[2]):
            return pid
    return None


def label_rect(rotation, disp_w, disp_h, w, h, margin=LABEL_MARGIN):
    """(x, y, w, h) on the panel for a label PNG of w x h (already turned for the panel) so it sits
    bottom-left for the viewer. The panel hangs `rotation` clockwise, so the viewer's bottom-left is
    the panel's bottom-left (0), bottom-right (90) or top-left (270)."""
    if rotation == 90:
        return disp_w - margin - w, disp_h - margin - h, w, h
    if rotation == 270:
        return margin, margin, w, h
    return margin, disp_h - margin - h, w, h


def png_size(data):
    """(width, height) from a PNG's IHDR."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def to_rotation(value):
    """0, 90 or 270 from whatever the CMS (or config) sent; anything else is 0."""
    try:
        r = int(value)
    except (TypeError, ValueError):
        return 0
    return r if r in ROTATIONS else 0


class DisplayError(Exception):
    pass


def _gst():
    global Gst
    if Gst is None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as G
        G.init(None)
        Gst = G
    return Gst


class Display:
    def __init__(self, sink="kmssink"):
        G = _gst()
        self.sink = sink
        self.clock = G.SystemClock.obtain()
        self.clock.set_property("clock-type", G.ClockType.REALTIME)  # clock time == time.time_ns()
        self.pipe = None
        self.still = False  # self.pipe is the screen pipeline (show_image)
        self.error = None
        self.epoch = self.duration = None  # ns, while a video plays
        self._offset = None  # pts - running time of the frames reaching the sink
        self.base = 0  # clock time (ns) of the first frame after the last join
        self._seg = None  # last segment past the parser (_loop_events)
        self._label = None  # (png, rotation) asked for while this video plays; set even if it failed
        self._osd = None  # the label pipeline
        self.fd = self._own_card() if sink.split()[0] == "kmssink" else -1

    @staticmethod
    def _own_card():
        """Open the vc4 card once: the agent stays DRM master, every kmssink shares this fd, and so
        can a second kmssink for the update label. -1 = kmssink opens its own (as before)."""
        card = vc4_card()
        kms = Gst.ElementFactory.make("kmssink")
        if not card or not kms or not kms.find_property("fd"):
            log.warning("display: no vc4 card or kmssink without fd (GStreamer < 1.22): no update label")
            return -1
        try:
            fd = os.open(card, os.O_RDWR | os.O_CLOEXEC)
        except OSError as e:
            log.warning("display: cannot open %s (%s): no update label", card, e)
            return -1
        log.info("display: DRM fd %d on %s, shared by every kmssink", fd, card)
        return fd

    def alive(self):
        return self.error is None

    def close(self):
        self._label_off()
        p, self.pipe, dur = self.pipe, None, self.duration
        self.still = False
        self.epoch = self.duration = None
        if p and dur:
            # A decoder drains at every loop end (SEGMENT_DONE) and needs the sink to keep taking
            # frames meanwhile. Stopping then deadlocked (sink paused, decoder waiting for a free
            # buffer, demuxer waiting for the decoder; seen on a playlist switch, which lands on a
            # loop end). A flushing seek first unblocks all of them.
            p.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
        if p:
            p.set_state(Gst.State.NULL)

    def _new(self, desc, location=None, src="filesrc name=src"):
        self.close()
        p = Gst.parse_launch(f"{src} ! {desc} ! {self.sink} name=vsink")
        if self.fd >= 0:  # kmssink forgets an external fd on stop: every new sink gets it again
            p.get_by_name("vsink").set_property("fd", self.fd)
        if location:
            p.get_by_name("src").set_property("location", location)
        p.use_clock(self.clock)
        self.pipe = p
        threading.Thread(target=self._watch, args=(p,), daemon=True).start()
        return p

    def _watch(self, p):
        T = Gst.MessageType
        bus = p.get_bus()
        while self.pipe is p:
            # Never block inside a GStreamer call from Python: it holds the GIL and starves the
            # streaming threads (seen: 3.5 fps instead of 25). Poll and sleep in Python instead.
            m = bus.pop_filtered(T.ERROR | T.EOS | T.SEGMENT_DONE)
            if m is None:
                time.sleep(0.02)
                continue
            if self.pipe is not p:
                break
            if m.type == T.SEGMENT_DONE and self.duration:
                # non-flushing: running time keeps counting, every loop adds exactly one duration
                p.seek(1.0, Gst.Format.TIME, Gst.SeekFlags.SEGMENT | Gst.SeekFlags.ACCURATE,
                       Gst.SeekType.SET, 0, Gst.SeekType.SET, self.duration)
            elif m.type != T.SEGMENT_DONE:
                self.error = m.parse_error()[0].message if m.type == T.ERROR else "unexpected end of stream"
                log.error("display: %s", self.error)

    def _state(self, p, st):
        if p.set_state(st) == Gst.StateChangeReturn.FAILURE:
            raise DisplayError(f"cannot go to {st.value_nick}: {self.error}")
        r = p.get_state(10 * Gst.SECOND)[0]
        if r != Gst.StateChangeReturn.SUCCESS:
            raise DisplayError(f"stuck going to {st.value_nick} ({r.value_nick}): {self.error}")

    def show_image(self, path):
        """Put a PNG on screen. The screen pipeline stays up and only gets the next PNG (the sink
        keeps the last frame), so going from screen to screen never shows the console."""
        with open(path, "rb") as f:
            data = f.read()
        if not self.still:
            p = self._new("pngdec ! videoconvert", src=STILL_SRC)
            if p.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:  # live: no preroll
                raise DisplayError(f"cannot show screens: {self.error}")
            self.still = True
        r = self.pipe.get_by_name("src").emit("push-buffer", Gst.Buffer.new_wrapped(data))
        if r != Gst.FlowReturn.OK:
            raise DisplayError(f"screen not accepted ({r.value_nick}): {self.error}")

    def play(self, video, epoch):
        """Start the looping video on the group clock, as is: a panel hung upright gets a file from
        the CMS that is already turned. Returns its duration (s)."""
        dec = HW_DECODE if Gst.ElementFactory.find("v4l2h264dec") else SW_DECODE
        p = self._new(f"qtdemux name=d d.video_0 ! {dec} ! {QUEUE}", video)
        p.set_start_time(Gst.CLOCK_TIME_NONE)  # we own the base time
        p.get_by_name("parse").get_static_pad("src").add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM | Gst.PadProbeType.EVENT_FLUSH, self._loop_events)
        self._state(p, Gst.State.PAUSED)
        ok, dur = p.query_duration(Gst.Format.TIME)
        if not ok or dur <= 0:
            raise DisplayError(f"no duration for {video}")
        self.epoch, self.duration = int(epoch * 1e9), dur
        self.join()
        return dur / 1e9

    def _loop_events(self, pad, info):
        """Fix up the loop between parser and decoder (events only: no per-frame Python).
        - Each loop's segment must start exactly one segment length after the previous one in
          running time. GStreamer 1.22 (Pi OS bookworm) puts it one frame short (base +5.960 s on a
          6 s file; 1.28 is right), so every loop crept a frame ahead until a visible re-join.
        The decoder still drains on SEGMENT_DONE at every loop end. Without that drain v4l2h264dec
        now and then lost the last ~2.7 s of a loop when the next loop's keyframe came in (frozen
        picture, then a re-join); with it, it loses 2 frames ("left undrained after CMD_STOP")."""
        e = info.get_event()
        if e.type == Gst.EventType.FLUSH_STOP:  # join(): a fresh timeline, take its segment as is
            self._seg = None
        elif e.type == Gst.EventType.SEGMENT:
            s, prev = e.parse_segment(), self._seg
            if prev is not None and prev.stop != Gst.CLOCK_TIME_NONE:
                want = prev.base + prev.stop - prev.start
                if s.base != want:
                    s.base = want
                    pad.push_event(Gst.Event.new_segment(s))  # comes back through here, base right
                    return Gst.PadProbeReturn.DROP
            self._seg = s
        return Gst.PadProbeReturn.OK

    def _probe(self, pad, info, seg):
        if info.type & Gst.PadProbeType.EVENT_DOWNSTREAM:
            e = info.get_event()
            if e.type == Gst.EventType.SEGMENT:
                seg["s"] = e.parse_segment()
        elif "s" in seg:
            b = info.get_buffer()
            if b.pts != Gst.CLOCK_TIME_NONE:
                self._offset = b.pts - seg["s"].to_running_time(Gst.Format.TIME, b.pts)
        return Gst.PadProbeReturn.OK

    def join(self, lead=1.0):
        """Seek onto the shared timeline and commit the matching base time."""
        p, epoch, dur = self.pipe, self.epoch, self.duration
        pad = p.get_by_name("vsink").get_static_pad("sink")
        for _ in range(3):
            target = self.clock.get_time() + int(lead * 1e9)
            pos = (target - epoch) % dur
            self._offset = None
            p.set_state(Gst.State.PAUSED)
            # Python probe only while seeking: a per-frame Python callback costs the GIL every frame
            probe = pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.EVENT_DOWNSTREAM, self._probe, {})
            p.seek(1.0, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE | Gst.SeekFlags.SEGMENT,
                   Gst.SeekType.SET, pos, Gst.SeekType.SET, dur)
            try:
                self._state(p, Gst.State.PAUSED)
            finally:
                pad.remove_probe(probe)
            if self._offset is None:
                raise DisplayError("no frame reached the screen after seeking")
            # where the demuxer really landed, not where we asked: running time 0 is that position
            base = sync.base_time_ns(epoch, dur, self._offset % dur, self.clock.get_time() + 200_000_000)
            if base <= target + 1_000_000_000:  # landed up to a frame late is fine; a loop late is not
                break
            lead *= 2  # seek + preroll overran the lead; try again with more room
        self.base = base
        p.set_base_time(base)
        self._state(p, Gst.State.PLAYING)
        log.info("joined the group clock at %.3f s, first frame in %.3f s",
                 (self._offset % dur) / 1e9, (base - self.clock.get_time()) / 1e9)

    def label(self, png, rotation=0):
        """Show the PNG (already turned for the panel) bottom-left for the viewer while the video
        plays, on its own plane; None = off. Main thread only (the pipelines live there). Called
        every tick: does nothing while nothing changed. Never raises: a failure is logged once per
        video and the video plays on."""
        want = (png, rotation) if png and self.duration else None
        if want == self._label:
            m = self._osd and self._osd.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if m:
                log.warning("update label: %s; video plays on without it", m.parse_error()[0].message)
                self._label_off(keep=True)
            return
        self._label_off()
        self._label = want
        if want:
            try:
                self._label_on(png, rotation)
            except Exception as e:  # noqa: BLE001 - the label must never stop the video
                log.warning("update label: %s; video plays on without it", e)
                self._label_off(keep=True)

    def _label_on(self, png, rotation):
        if self.fd < 0:
            raise DisplayError("no shared DRM fd")
        with open(png, "rb") as f:
            data = f.read()
        vsink = self.pipe.get_by_name("vsink")
        vplane = vsink.get_property("plane-id")
        plane = pick_plane(overlay_planes(self.fd), vplane)
        if plane is None:
            raise DisplayError(f"no free overlay plane above the video's ({vplane})")
        x, y, w, h = label_rect(rotation, vsink.get_property("display-width"),
                                vsink.get_property("display-height"), *png_size(data))
        p = Gst.parse_launch(f"{STILL_SRC} ! pngdec ! videoconvert ! video/x-raw,format=BGRA ! {self.sink} "
                             f'name=osd sync=false skip-vsync=true plane-id={plane} render-rectangle="<{x},{y},{w},{h}>"')
        # skip-vsync: a vblank wait would drain the video sink's events on the shared fd and hang it
        p.get_by_name("osd").set_property("fd", self.fd)
        self._osd = p
        if p.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise DisplayError("label pipeline cannot start")
        r = p.get_by_name("src").emit("push-buffer", Gst.Buffer.new_wrapped(data))
        if r != Gst.FlowReturn.OK:
            raise DisplayError(f"label not accepted ({r.value_nick})")
        log.info("update label on plane %d at %d,%d %dx%d (video on plane %d)", plane, x, y, w, h, vplane)

    def _label_off(self, keep=False):
        """Stop the label pipeline: its framebuffer goes, which takes the plane off. keep = remember
        what was asked, so a failed label is not retried every tick."""
        p, self._osd = self._osd, None
        if not keep:
            self._label = None
        if p:
            p.set_state(Gst.State.NULL)
            log.info("update label off")

    def drift(self):
        """Playback position minus the group target (s); None if unknown or not started yet."""
        started = self.pipe and self.duration and self.clock.get_time() > self.base
        ok, pos = self.pipe.query_position(Gst.Format.TIME) if started else (False, 0)
        if not ok:
            return None
        return sync.drift((pos % self.duration) / 1e9, sync.target_position(time.time(), self.epoch / 1e9,
                                                                            self.duration / 1e9), self.duration / 1e9)

    def stats(self):
        """kmssink 'rendered=…, dropped=…' (str parsing: Gst 1.26 GI can't read structure fields)."""
        try:
            s = self.pipe.get_by_name("vsink").get_property("stats") if self.pipe else None
        except TypeError:  # a dev sink (autovideosink bin) without the property
            s = None
        return dict(re.findall(r"(rendered|dropped)=\(guint64\)(\d+)", s.to_string())) if s else {}


def _check_label_failure():
    """A Pi without a free overlay plane (or any label error): a warning, the video plays on, and
    the label is not retried every tick. No GStreamer needed: the failure comes before the pipeline."""
    import tempfile

    class Sink:
        def get_property(self, k):
            return {"plane-id": 52, "display-width": 1920, "display-height": 1080}[k]

    class Pipe:
        def get_by_name(self, name):
            return Sink()

    asked, real = [], globals()["overlay_planes"]

    def busy(fd):
        asked.append(fd)
        return [(52, 91, 1, True), (53, 91, 1, True)]  # the only other plane is taken

    d = Display.__new__(Display)
    d.pipe, d.duration, d.fd, d._label, d._osd = Pipe(), 6_000_000_000, 7, None, None
    globals()["overlay_planes"] = busy
    logging.disable(logging.WARNING)
    try:
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 300, 50))
            f.flush()
            for _ in range(3):  # every tick of play_synced
                d.label(f.name, 0)
            assert asked == [7] and d._osd is None and d._label == (f.name, 0)
            d.label(None)  # update over: forgotten
            assert d._label is None
            d.fd = -1  # no shared DRM fd (old GStreamer, no vc4): same, without touching the planes
            d.label(f.name, 90)
            assert asked == [7] and d._osd is None and d._label == (f.name, 90)
            d.duration = None  # a setup screen instead of the video: never a label
            d.label(f.name, 90)
            assert d._label is None
    finally:
        globals()["overlay_planes"] = real
        logging.disable(logging.NOTSET)
    print("  update label: a failure is a warning, once, and the video plays on")


def _selfcheck():
    # update label: bottom-left for the viewer, fully on the panel; label PNG is already turned
    assert label_rect(0, 1920, 1080, 300, 50) == (54, 1080 - 54 - 50, 300, 50)
    assert label_rect(90, 1920, 1080, 50, 300) == (1920 - 54 - 50, 1080 - 54 - 300, 50, 300)
    assert label_rect(270, 1920, 1080, 50, 300) == (54, 54, 50, 300)
    for r, size in ((0, (300, 50)), (90, (50, 300)), (270, (50, 300))):
        x, y, w, h = label_rect(r, 1920, 1080, *size)
        assert 0 <= x and 0 <= y and x + w <= 1920 and y + h <= 1080, r
    planes = [(40, 0, 0x3, True), (52, 91, 0x3, True), (60, 0, 0x3, False), (70, 0, 0x4, True), (80, 0, 0x3, True)]
    assert pick_plane(planes, 52) == 80  # not taken, ARGB, same CRTCs, above the video
    assert pick_plane(planes, 80) is None
    assert pick_plane([(52, 91, 1, True), (53, 91, 1, True)], 52) is None  # taken
    assert png_size(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 3, 2)) == (3, 2)
    _check_label_failure()
    assert [to_rotation(v) for v in (0, 90, 270, "90", 180, -90, None, "x", 90.0)] == [0, 90, 270, 90, 0, 0, 0, 0, 90]
    try:
        G = _gst()
    except (ImportError, ValueError):
        print("gst self-check OK (rotation only: no GStreamer here)")
        return
    # the video pipeline never turns anything: decoded frames go straight to the sink
    p = G.parse_launch(f"videotestsrc num-buffers=1 ! video/x-raw,width=192,height=108 ! {QUEUE} ! fakesink name=vsink")
    assert p.get_by_name("vsink") is not None
    p.set_state(G.State.NULL)
    # screens: every PNG goes through the same pipeline and reaches the sink
    import tempfile
    import zlib

    def png(rgb):
        def chunk(t, b):
            return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
        rows = b"".join(b"\0" + bytes(rgb) * 16 for _ in range(9))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 9, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    def shown():
        sample = d.pipe.get_by_name("vsink").get_property("last-sample")
        return sample and sample.get_buffer().extract_dup(0, 3)

    d = Display(sink="fakesink")
    with tempfile.TemporaryDirectory() as tmp:
        first = None
        for i, rgb in enumerate(((255, 0, 0), (0, 0, 255), (0, 255, 0))):
            path = os.path.join(tmp, f"{i}.png")
            with open(path, "wb") as f:
                f.write(png(rgb))
            d.show_image(path)
            first = first or d.pipe
            deadline = time.monotonic() + 5
            while shown() != bytes(rgb) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert shown() == bytes(rgb), (rgb, shown(), d.error)
            assert d.pipe is first and d.alive()
    d.close()
    print("  screens: one pipeline, every PNG shown")
    print("gst self-check OK")


if __name__ == "__main__":
    _selfcheck()
