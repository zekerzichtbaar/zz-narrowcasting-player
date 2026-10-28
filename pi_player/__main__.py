"""pi-player agent entrypoint.

  python3 -m pi_player [--config PATH] [--video PATH]   run the agent (systemd does this)
  pi-player reset [--config PATH]                       forget pairing + content -> first-boot flow
"""
import argparse
import faulthandler
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

from . import __version__, bell, blesetup, config, content as content_mod, heartbeat, netman, pairing, screens, state, updater
from .gst import Display, DisplayError, quiet_console, to_rotation

log = logging.getLogger("pi-player")

# ponytail: kmssink's frame counter as the stall check; a video with one frame held longer than
# this (variable frame rate stills) would trip it, the CMS encodes a fixed frame rate.
STALL = 10.0  # s without a new frame on HDMI while playing -> exit, systemd restarts us


def alive():
    """systemd watchdog ping (WatchdogSec in the unit). Only the main loop calls this, so a main
    thread stuck in GStreamer (seen: v4l2h264dec teardown hanging on a playlist switch) stops the
    pings and systemd kills + restarts the agent. No NOTIFY_SOCKET (dev) = no-op."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect("\0" + addr[1:] if addr[0] == "@" else addr)
            s.sendall(b"WATCHDOG=1")
    except OSError as e:
        log.warning("watchdog ping failed: %s", e)


def runtime_dir():
    """Where the setup-screen PNGs go: systemd's RuntimeDirectory, or a temp dir on dev machines."""
    for d in ("/run/pi-player", os.path.join(tempfile.gettempdir(), "pi-player")):
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            pass


def play_synced(display, cfg, content=None, keep_playing=lambda: True, tick=lambda: None):
    """Play the video locked to the group clock until a new version is due ("switch") or
    keep_playing() turns false ("leave"). GStreamer does the frame timing; we only watch. tick()
    runs every check (the update label)."""
    video = cfg["video"]
    man = content.current_manifest() if content and video == content.current else None
    # Every screen counts loops from the same fixed epoch (config, 0 = 1970), never the manifest's:
    # the CMS gave two screens of one group different epochs (12.5 s apart). Same clock (chrony) +
    # same duration = same frame, whatever the CMS sends.
    epoch = float(cfg["epoch"])
    duration = display.play(video, epoch)
    if man and abs(float(man["duration"]) - duration) > 0.05:
        log.warning("manifest duration %.3f s != file duration %.3f s; looping on the file's",
                    float(man["duration"]), duration)
    log.info("playing %s (version %s, duration %.3f s, epoch %s)", video, man and man["version"],
             duration, epoch)
    heartbeat.STATUS["state"] = "PLAYING"
    th, last_log = cfg["sync"], 0.0
    frames, moved = None, time.monotonic()
    while True:
        alive()
        if not display.alive():
            raise DisplayError(display.error)
        if not keep_playing():
            return "leave"
        tick()
        d = display.drift()
        heartbeat.STATUS["drift_ms"] = None if d is None else round(d * 1000, 1)
        if d is not None and abs(d) > th["seek_threshold"]:  # clock stepped, pipeline stalled, ...
            log.warning("drift %+.1f ms -> re-joining the group clock", d * 1000)
            display.join()
            moved = time.monotonic()
        # the decoder can hang without an error: the picture freezes, position/drift go quiet
        rendered = display.stats().get("rendered")  # None on dev sinks: no check
        if rendered != frames:
            frames, moved = rendered, time.monotonic()
        elif rendered is not None and time.monotonic() - moved > STALL:
            raise DisplayError(f"no new frame for {STALL:.0f} s (rendered stuck at {rendered})")
        now = time.time()
        if now - last_log >= 60:
            log.info("drift %s ms, frames this loop %s", heartbeat.STATUS["drift_ms"], display.stats())
            last_log = now
        due = content.due(now) if content else None
        if due is not None and due <= 0:
            return "switch"
        time.sleep(th["interval"] if due is None else max(0.0, min(th["interval"], due)))


def switch(content, cfg):
    m = content.activate()
    if m:
        cfg["video"] = content.current
        log.info("activated version %s at %.3f (activate_at %s)", m["version"], time.time(), m.get("activate_at"))


class Agent:
    """Main loop: evaluate the state each tick, put the matching screen or the video on HDMI."""

    def __init__(self, cfg, display, content, pair, monitor=None, wifi=None, ble=None, upd=None):
        self.cfg, self.display, self.content, self.pair = cfg, display, content, pair
        self.monitor, self.wifi, self.ble, self.upd = monitor, wifi, ble, upd
        self.labels = {}  # rotation -> rendered update label PNG
        self.screen_dir = runtime_dir()
        self.footer = f"{pair.device_id if pair else pairing.device_id()} · pi-player {__version__}"
        self.timer = state.OfflineTimer(time.monotonic())  # monotonic: no RTC, NTP jumps the wall clock days
        self.setup = state.SetupWindow(time.monotonic(), float(cfg["offline_timeout"]))
        self.setup_left = 0.0
        self.wifi_asked = 0  # the CMS's last "Wifi opnieuw instellen" we acted on
        self.was_online = True
        self.shown = None  # (key, rotation) of the screen on HDMI; None = video or nothing

    def rotation(self):
        """How the panel hangs: from the manifest (kept in state.json), else the config (dev)."""
        return self.content.rotation() if self.content else to_rotation(self.cfg.get("rotation"))

    def state(self):
        now = time.monotonic()
        has_video = os.path.exists(self.cfg["video"])
        if self.content is None:  # dev: no state_dir, local file only
            return state.PLAYING if has_video else state.WAITING
        net = self.monitor.ok if self.monitor else True
        linked = self.monitor.linked if self.monitor else True
        if net and not self.was_online and self.setup.forced:
            # Behind our own hotspot the CMS can't reach us: on each brief retry of the saved wifi
            # fetch the manifest now, it may say "Wifi-scherm stoppen".
            self.content.poke()
        self.was_online = net
        asked = self.content.wifi_setup_request()
        if asked == 0 and self.setup.forced:
            self.setup.close()
            log.info("wifi setup stopped by the CMS")
        elif self.wifi and asked and asked != self.wifi_asked:
            # ponytail: remembered in memory only; an agent restart while the CMS still sends the same
            # request (5 min) opens the setup once more
            self.wifi_asked = asked
            self.wifi.joined = None  # a network joined in an earlier setup must not end this one
            self.setup.open(now, float(self.cfg["offline_timeout"]))
            log.info("wifi setup asked by the CMS")
        if self.wifi and 0 < self.setup_left < 60 and self.wifi.clients():
            self.setup.hold(now, 60)  # ponytail: iw only in the last minute, the countdown stays at 1 min
        joined = bool(self.wifi and self.wifi.joined)
        self.setup_left = self.setup.update(net, now, joined)
        return state.decide(net, linked, bool(self.content.token()), has_video, self.timer.update(net, now), now,
                            self.setup_left, self.setup.forced)

    def screen_key(self, st):
        if st == state.PAIRING:
            info = self.pair.info if self.pair else None
            return (st,) + tuple((info or {}).get(k) for k in ("code", "pair_url", "expires_at"))
        if st == state.WAITING:
            return (st, self.content.screen_name() if self.content else None)
        if st == state.WIFI_SETUP and self.wifi:
            # countdown in whole minutes (one re-render a minute), only when content waits behind it
            resume = -(-int(self.setup_left) // 60) if self.setup_left > 0 and self.content.token() \
                and os.path.exists(self.cfg["video"]) else None
            return (st,) + self.wifi.info() + (self.ble.url if self.ble and self.ble.registered else None, resume)
        return (st,)

    def show(self, key):
        rot = self.rotation()
        if (key, rot) == self.shown:
            return
        st, path = key[0], os.path.join(self.screen_dir, key[0].lower() + ".png")
        common = {"footer": self.footer, "logo": self.cfg.get("logo"), "rotation": rot}
        try:
            if st == state.PAIRING:
                screens.pairing(path, *key[1:], **common)
            elif st == state.WAITING:
                screens.waiting(path, key[1], **common)
            elif st == "IDENTIFY":
                screens.identify(path, key[1], **common)
            else:
                screens.wifi_setup(path, *key[1:5], **common, portal_url="http://" + netman.HOTSPOT_IP,
                                   setup_url=key[5] if len(key) > 5 else None, resume=key[6] if len(key) > 6 else None)
        except Exception as e:  # e.g. Pillow/qrcode missing on a dev box: at least blank the video
            log.error("rendering %s screen failed: %s", st, e)
            self.display.close()
        else:
            self.display.show_image(path)
            log.info("showing %s screen %s (rotation %s)", st, path, rot)
        self.shown = (key, rot)

    def label(self):
        """"Aan het updaten..." bottom-left over the playing video while the update runner works
        (until install.sh restarts us). Its own plane: the video stays zero-copy. Never fatal."""
        png, rot = None, self.rotation()
        if self.upd and self.upd.updating:
            png = self.labels.get(rot)
            if rot not in self.labels:
                try:
                    png = screens.update_label(os.path.join(self.screen_dir, f"update-label-{rot}.png"), rot)
                except Exception as e:  # e.g. Pillow missing on a dev box
                    log.warning("rendering the update label failed: %s", e)
                self.labels[rot] = png
        self.display.label(png, rot)

    def identifying(self):
        """"Scherm identificeren": (name, ms left) while the CMS asks this screen to show its name."""
        if not self.content:
            return None
        return content_mod.identify_osd(self.content.screen_name(), self.content.identify_until(), time.time())

    def loop(self):
        last = None
        while True:
            alive()
            if not self.display.alive():
                raise DisplayError(self.display.error)
            st = self.state()
            if st != last:
                log.info("state %s -> %s", last, st)
                last = st
            heartbeat.STATUS["state"] = st
            if self.wifi:
                self.wifi.want(st == state.WIFI_SETUP)
            if st != state.PLAYING:
                heartbeat.STATUS["drift_ms"] = None
            due = self.content.due(time.time()) if self.content else None
            if due is not None and due <= 0:
                switch(self.content, self.cfg)
                continue
            ident = self.identifying()
            if ident:
                # ponytail: full-screen name card instead of an overlay; a text overlay on the
                # video would cost the zero-copy hardware decode. Video re-joins the clock after.
                self.show(("IDENTIFY", ident[0], self.content.identify_until()))
                time.sleep(1)
                continue
            if st == state.PLAYING:
                self.shown = None
                # The video is never turned here: for a panel at 90/270 the CMS serves a turned
                # file, so a new rotation arrives as a new version (download + switch).
                if play_synced(self.display, self.cfg, self.content,
                               lambda: self.state() == state.PLAYING and not self.identifying(),
                               self.label) == "switch":
                    switch(self.content, self.cfg)
                continue
            self.show(self.screen_key(st))
            time.sleep(1)


def run(cfg):
    if cfg["video_sink"].startswith("kmssink"):  # we own the HDMI console
        quiet_console()
    display = Display(cfg["video_sink"])

    def stop(*_):
        display.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        log.info("pi-player %s, display via %s", __version__, cfg["video_sink"])
        content = pair = monitor = wifi = ble = upd = None
        try:
            content = content_mod.Content(cfg)
            content.start()
        except OSError as e:
            log.warning("content updates disabled (%s); playing local file only", e)
        if content:
            pair = pairing.Pairing(cfg, content)
            pair.start()
            heartbeat.Heartbeat(cfg, content).start()
            bell.Bell(cfg, content).start()  # poll/identify -> manifest now, not at the next poll
            upd = updater.Updater(cfg, content).start()  # "Speler bijwerken": runner in its own unit
            monitor = netman.Monitor(cfg["cms_url"]).start()
            if netman.available():
                wifi = netman.WifiSetup(pair.device_id, cfg["wifi_retry"], monitor).start()
                if cfg["setup_url"] and blesetup.available():
                    ble = blesetup.BleSetup(wifi, pair.device_id, cfg["setup_url"], pairing=lambda: pair.info,
                                            paired=lambda: bool(content.token())).start()
            else:
                log.warning("nmcli not found: no wifi setup hotspot (dev machine?)")
        Agent(cfg, display, content, pair, monitor, wifi, ble, upd).loop()
    except DisplayError as e:
        # Let systemd restart the whole agent (fresh pipeline, fresh state) — simplest recovery.
        log.error("display: %s; exiting for restart", e)
        display.close()
        sys.exit(1)


def reset(cfg):
    """Wipe token + pairing info + videos + the wifi networks saved via the setup portal."""
    for p in content_mod.reset(cfg["state_dir"]):
        print("removed", p)
    for name in netman.forget_ours():
        print("removed wifi connection", name)
    if os.geteuid() == 0 and shutil.which("systemctl"):
        subprocess.run(["systemctl", "try-restart", "pi-player.service"], check=False)
        print("pi-player restarted: the screen shows the first-boot flow")
    else:
        print("not root: restart the agent yourself (a running agent notices the missing token)")


def main():
    p = argparse.ArgumentParser(prog="pi-player")
    p.add_argument("command", nargs="?", choices=["run", "reset"], default="run")
    p.add_argument("--config", help="config file (default /etc/pi-player/config.toml)")
    p.add_argument("--video", help="override video path")
    p.add_argument("--epoch", type=float, help="override sync epoch")
    p.add_argument("--rotation", type=int, choices=[0, 90, 270],
                   help="panel hung this many degrees clockwise, turns the setup screens only (without content updates; the CMS decides otherwise)")
    a = p.parse_args()
    faulthandler.enable()  # watchdog kill (SIGABRT): every thread's Python stack in the journal
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load(a.config)
    if a.command == "reset":
        return reset(cfg)
    if a.video:
        cfg["video"] = a.video
    if a.epoch is not None:
        cfg["epoch"] = a.epoch
    if a.rotation is not None:
        cfg["rotation"] = a.rotation
    run(cfg)


if __name__ == "__main__":
    main()
