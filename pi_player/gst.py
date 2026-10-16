"""Everything on HDMI goes through GStreamer: the looping video and the setup-screen PNGs.

One pipeline at a time owns the screen. The screen pipeline stays up from one PNG to the next
(appsrc), so changing screens never leaves HDMI empty; only screen <-> video swaps pipelines, and
quiet_console() makes that moment black instead of the tty1 console.

The video pipeline runs on the system realtime clock (chrony keeps it equal on every Pi) with a
base time chosen so that media position p plays at epoch + p (see sync.py). It loops with
non-flushing segment seeks: no gap, no stall and no re-sync at the loop boundary.
"""
import fcntl
import logging
import os
import re
import threading
import time

from . import sync

log = logging.getLogger("pi-player")
Gst = None

# The Pi 4 hardware decoder refuses streams without colour tags (common in CMS/ffmpeg exports)
# with "not-negotiated"; tagging them bt709 makes it accept them. Zero-copy into kmssink.
HW_DECODE = "h264parse ! capssetter caps=video/x-h264,colorimetry=bt709 ! v4l2h264dec"
SW_DECODE = "h264parse ! avdec_h264 ! videoconvert"  # dev machines / no v4l2 decoder
QUEUE = "queue max-size-time=2000000000 max-size-bytes=0 max-size-buffers=0"

# Rotation (CMS `screens.rotation`) = how the panel hangs, turned clockwise. The picture turns back
# the other way (same as the Chromium player's CSS rotate(-rotation)): 90 -> 90 degrees counter-clockwise.
# The Pi 4 display (vc4 HVS) can only flip planes, not turn them 90 degrees, so KMS plane rotation
# is out. First choice is the GPU: decoded frames go into GL (dmabuf import), glvideoflip turns them
# and glimagesink shows them via GBM/KMS. If GL is missing or does not preroll: videoflip on the CPU
# (a 1080p25 transpose, roughly a third of one core; only rotated screens pay it).
FLIP = {90: "90l", 270: "90r"}
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


def to_rotation(value):
    """0, 90 or 270 from whatever the CMS (or config) sent; anything else is 0."""
    try:
        r = int(value)
    except (TypeError, ValueError):
        return 0
    return r if r in FLIP else 0


def rotate_fragment(rotation, gl):
    """Pipeline piece between the decoded frames and the sink that turns the picture upright
    on a panel hung at `rotation`; "" when nothing needs turning."""
    d = FLIP.get(to_rotation(rotation))
    if not d:
        return ""
    if gl:
        return f"glupload ! glcolorconvert ! glvideoflip video-direction={d}"
    return f"videoflip video-direction={d}"


class DisplayError(Exception):
    pass


def _gst():
    global Gst
    if Gst is None:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            # console Pi: GL straight on the display (GBM/KMS), for glimagesink on rotated screens
            for k, v in (("GST_GL_WINDOW", "gbm"), ("GST_GL_PLATFORM", "egl"), ("GST_GL_API", "gles2")):
                os.environ.setdefault(k, v)
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as G
        G.init(None)
        Gst = G
    return Gst


class Display:
    def __init__(self, sink="kmssink", gl_sink="glimagesink", rotate_method="gl"):
        G = _gst()
        self.sink, self.gl_sink = sink, gl_sink
        # GPU rotation unless configured off or the GL elements are not installed
        self.gl = rotate_method == "gl" and all(G.ElementFactory.find(e) for e in
                                                ("glupload", "glvideoflip", gl_sink.split()[0]))
        self.clock = G.SystemClock.obtain()
        self.clock.set_property("clock-type", G.ClockType.REALTIME)  # clock time == time.time_ns()
        self.pipe = None
        self.still = False  # self.pipe is the screen pipeline (show_image)
        self.error = None
        self.epoch = self.duration = None  # ns, while a video plays
        self._offset = None  # pts - running time of the frames reaching the sink
        self.base = 0  # clock time (ns) of the first frame after the last join

    def alive(self):
        return self.error is None

    def close(self):
        p, self.pipe = self.pipe, None
        self.still = False
        self.epoch = self.duration = None
        if p:
            p.set_state(Gst.State.NULL)

    def _new(self, desc, location=None, sink=None, src="filesrc name=src"):
        self.close()
        p = Gst.parse_launch(f"{src} ! {desc} ! {sink or self.sink} name=vsink")
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

    def play(self, video, epoch, rotation=0):
        """Start the looping video on the group clock, turned for a panel hung at `rotation`.
        Returns its duration (s)."""
        dec = HW_DECODE if Gst.ElementFactory.find("v4l2h264dec") else SW_DECODE
        gl = self.gl and to_rotation(rotation) != 0
        turn = rotate_fragment(rotation, gl)
        p = self._new(f"qtdemux name=d d.video_0 ! {dec} ! {QUEUE}" + (f" ! {turn}" if turn else ""), video,
                      self.gl_sink if gl else None)
        p.set_start_time(Gst.CLOCK_TIME_NONE)  # we own the base time
        try:
            self._state(p, Gst.State.PAUSED)
        except DisplayError as e:
            if not gl:
                raise
            log.warning("GPU rotation failed (%s); turning on the CPU from now on", e)
            self.close()
            self.error, self.gl = None, False
            return self.play(video, epoch, rotation)
        ok, dur = p.query_duration(Gst.Format.TIME)
        if not ok or dur <= 0:
            raise DisplayError(f"no duration for {video}")
        self.epoch, self.duration = int(epoch * 1e9), dur
        self.join()
        return dur / 1e9

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
        except TypeError:  # a sink bin (glimagesink) without the property
            s = None
        return dict(re.findall(r"(rendered|dropped)=\(guint64\)(\d+)", s.to_string())) if s else {}


def _selfcheck():
    assert [to_rotation(v) for v in (0, 90, 270, "90", 180, -90, None, "x", 90.0)] == [0, 90, 270, 90, 0, 0, 0, 0, 90]
    assert rotate_fragment(0, True) == rotate_fragment(0, False) == rotate_fragment(180, True) == ""
    # hung clockwise -> picture counter-clockwise (90l), like Chromium's rotate(-90deg)
    assert rotate_fragment(90, True) == "glupload ! glcolorconvert ! glvideoflip video-direction=90l"
    assert rotate_fragment(270, True) == "glupload ! glcolorconvert ! glvideoflip video-direction=90r"
    assert rotate_fragment(90, False) == "videoflip video-direction=90l"
    assert rotate_fragment("270", False) == "videoflip video-direction=90r"
    try:
        G = _gst()
    except (ImportError, ValueError):
        print("gst self-check OK (fragments only: no GStreamer here)")
        return
    # every fragment parses with the elements this machine has (videotestsrc instead of the file)
    for rot in (0, 90, 270):
        for gl in (False, True):
            frag = rotate_fragment(rot, gl)
            need = [e.split()[0] for e in frag.split(" ! ")] if frag else []
            if not all(G.ElementFactory.find(e) for e in need):
                print(f"  skip rotation {rot} gl={gl}: missing {[e for e in need if not G.ElementFactory.find(e)]}")
                continue
            p = G.parse_launch("videotestsrc num-buffers=1 ! video/x-raw,width=192,height=108"
                               + (f" ! {frag}" if frag else "") + " ! fakesink name=vsink")
            assert p.get_by_name("vsink") is not None
            if not gl:  # CPU flip: check the frame really turned (192x108 -> 108x192)
                caps = {}
                pad = p.get_by_name("vsink").get_static_pad("sink")

                def keep(pd, _info):
                    caps.setdefault("c", pd.get_current_caps())
                    return G.PadProbeReturn.OK
                pad.add_probe(G.PadProbeType.BUFFER, keep)
                p.set_state(G.State.PLAYING)
                p.get_bus().timed_pop_filtered(5 * G.SECOND, G.MessageType.EOS | G.MessageType.ERROR)
                p.set_state(G.State.NULL)
                st = caps["c"].get_structure(0)
                size = (st.get_value("width"), st.get_value("height"))
                assert size == ((192, 108) if rot == 0 else (108, 192)), (rot, size)
            print(f"  rotation {rot} gl={gl}: ok")
    # screens: every PNG goes through the same pipeline and reaches the sink
    import struct
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

    d = Display(sink="fakesink", gl_sink="fakesink", rotate_method="cpu")
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
