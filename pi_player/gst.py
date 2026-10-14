"""Everything on HDMI goes through GStreamer: the looping video and the setup-screen PNGs.

One pipeline at a time owns the screen. The video pipeline runs on the system realtime clock
(chrony keeps it equal on every Pi) with a base time chosen so that media position p plays at
epoch + p (see sync.py). It loops with non-flushing segment seeks: no gap, no stall and no
re-sync at the loop boundary.
"""
import logging
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
        self.error = None
        self.epoch = self.duration = None  # ns, while a video plays
        self._offset = None  # pts - running time of the frames reaching the sink
        self.base = 0  # clock time (ns) of the first frame after the last join

    def alive(self):
        return self.error is None

    def close(self):
        p, self.pipe = self.pipe, None
        self.epoch = self.duration = None
        if p:
            p.set_state(Gst.State.NULL)

    def _new(self, desc, location):
        self.close()
        p = Gst.parse_launch(f"filesrc name=src ! {desc} ! {self.sink} name=vsink")
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
        """Put a PNG on screen; the prerolled frame stays up while PAUSED."""
        self._state(self._new("pngdec ! videoconvert", path), Gst.State.PAUSED)

    def play(self, video, epoch):
        """Start the looping video on the group clock. Returns its duration (s)."""
        dec = HW_DECODE if Gst.ElementFactory.find("v4l2h264dec") else SW_DECODE
        p = self._new(f"qtdemux name=d d.video_0 ! {dec} ! {QUEUE}", video)
        p.set_start_time(Gst.CLOCK_TIME_NONE)  # we own the base time
        self._state(p, Gst.State.PAUSED)
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
        s = self.pipe.get_by_name("vsink").get_property("stats") if self.pipe else None
        return dict(re.findall(r"(rendered|dropped)=\(guint64\)(\d+)", s.to_string())) if s else {}
