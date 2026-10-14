"""pi-player agent entrypoint.

  python3 -m pi_player [--config PATH] [--video PATH]   run the agent (systemd does this)
  pi-player reset [--config PATH]                       forget pairing + content -> first-boot flow
"""
import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import __version__, config, content as content_mod, heartbeat, netman, pairing, screens, state, sync
from .mpv import Mpv, MpvError

log = logging.getLogger("pi-player")


def socket_path(wanted):
    try:
        os.makedirs(os.path.dirname(wanted), exist_ok=True)
        return wanted
    except OSError:  # dev machine without /run access
        return os.path.join(tempfile.gettempdir(), "pi-player-mpv.sock")


def wait_for_duration(mpv, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            d = mpv.duration()
            if d:
                return float(d)
        except MpvError:
            pass  # property unavailable until the file is loaded
        time.sleep(0.1)
    raise MpvError("video duration unavailable (file not playable?)")


def play_synced(mpv, cfg, content=None, keep_playing=lambda: True, tick=lambda: None):
    """Load the video and keep it on the group clock. tick() runs every round (identifying).

    Returns "switch" when a new version is due, "leave" when keep_playing() turns false."""
    th = cfg["sync"]
    video = cfg["video"]
    mpv.loadfile(video)
    mpv.set_speed(1.0)
    duration = wait_for_duration(mpv)
    epoch = float(cfg["epoch"])
    man = content.current_manifest() if content and video == content.current else None
    if man:  # manifest is authoritative for the group clock
        epoch, duration = float(man.get("epoch") or 0), float(man["duration"])
    log.info("playing %s (version %s, duration %.3f s, epoch %s)",
             video, man and man["version"], duration, epoch)
    speed = 1.0
    last_log = 0.0
    heartbeat.STATUS["state"] = "PLAYING"
    while True:
        if not mpv.alive():
            raise MpvError("mpv died")
        if not keep_playing():
            return "leave"
        tick()
        t0 = time.time()
        pos = mpv.time_pos()
        now = (t0 + time.time()) / 2  # midpoint of the IPC round trip
        if pos is not None:
            d = sync.drift(float(pos), sync.target_position(now, epoch, duration), duration)
            heartbeat.STATUS["drift_ms"] = d * 1000
            action = sync.correction(d, th)
            if action[0] == "seek":
                target = sync.target_position(time.time(), epoch, duration)
                log.info("drift %+.1f ms -> seek to %.3f", d * 1000, target)
                mpv.seek(target)
                if speed != 1.0:
                    mpv.set_speed(1.0)
                    speed = 1.0
            else:
                new = action[1] if action[0] == "speed" else 1.0
                if new != speed:
                    mpv.set_speed(new)
                    speed = new
                if action[0] == "speed" or t0 - last_log >= 10:
                    log.info("drift %+.1f ms speed %.4f", d * 1000, speed)
                    last_log = t0
        due = content.due(time.time()) if content else None
        if due is not None and due <= 0:
            return "switch"
        time.sleep(th["interval"] if due is None else min(th["interval"], due))


def switch(content, cfg):
    m = content.activate()
    if m:
        cfg["video"] = content.current
        log.info("activated version %s at %.3f (activate_at %s)", m["version"], time.time(), m.get("activate_at"))


class Agent:
    """Main loop: evaluate the state each tick, put the matching screen or the video on mpv."""

    def __init__(self, cfg, mpv, content, pair, monitor=None, wifi=None):
        self.cfg, self.mpv, self.content, self.pair = cfg, mpv, content, pair
        self.monitor, self.wifi = monitor, wifi
        self.screen_dir = os.path.dirname(mpv.socket_path)
        self.footer = f"{pair.device_id if pair else pairing.device_id()} · pi-player {__version__}"
        self.timer = state.OfflineTimer(time.time())
        self.shown = None  # key of the screen on mpv; None = video or nothing
        self.identified = 0  # identify_until already put on the OSD

    def state(self):
        now = time.time()
        has_video = os.path.exists(self.cfg["video"])
        if self.content is None:  # dev: no state_dir, local file only
            return state.PLAYING if has_video else state.WAITING
        net = self.monitor.ok if self.monitor else True
        return state.decide(net, bool(self.content.token()), has_video, self.timer.update(net, now), now,
                            float(self.cfg["offline_timeout"]))

    def screen_key(self, st):
        if st == state.PAIRING:
            info = self.pair.info if self.pair else None
            return (st,) + tuple((info or {}).get(k) for k in ("code", "pair_url", "expires_at"))
        if st == state.WAITING:
            return (st, self.content.screen_name() if self.content else None)
        if st == state.WIFI_SETUP and self.wifi:
            return (st,) + self.wifi.info()
        return (st,)

    def show(self, key):
        if key == self.shown:
            return
        st, path = key[0], os.path.join(self.screen_dir, key[0].lower() + ".png")
        common = {"footer": self.footer, "logo": self.cfg.get("logo")}
        try:
            if st == state.PAIRING:
                screens.pairing(path, *key[1:], **common)
            elif st == state.WAITING:
                screens.waiting(path, key[1], **common)
            else:
                screens.wifi_setup(path, *key[1:], **common, portal_url="http://" + netman.HOTSPOT_IP)
            self.mpv.loadfile(path)
            log.info("showing %s screen %s", st, path)
        except MpvError:
            raise
        except Exception as e:  # e.g. Pillow/qrcode missing on a dev box: at least blank the video
            log.error("rendering %s screen failed: %s", st, e)
            self.mpv.command("stop")
        self.shown = key

    def identify(self):
        """"Scherm identificeren": the screen name on the OSD until identify_until, once per value
        (mpv keeps it up for the duration, also over a loadfile)."""
        until = self.content.identify_until() if self.content else 0
        if until == self.identified:
            return
        self.identified = until
        osd = content_mod.identify_osd(self.content.screen_name(), until, time.time())
        if osd:
            self.mpv.show_text(*osd)
            log.info("identifying as %r for %.0f s", osd[0], osd[1] / 1000)

    def loop(self):
        last = None
        while True:
            if not self.mpv.alive():
                raise MpvError("mpv died")
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
            if st == state.PLAYING:
                self.shown = None
                if play_synced(self.mpv, self.cfg, self.content,
                               lambda: self.state() == state.PLAYING, self.identify) == "switch":
                    switch(self.content, self.cfg)
                continue
            self.show(self.screen_key(st))
            self.identify()
            time.sleep(1)


def run(cfg):
    mpv = Mpv(socket_path(cfg["mpv_socket"]), cfg["mpv_bin"], cfg["mpv_extra_args"])

    def stop(*_):
        mpv.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        mpv.start()
        log.info("pi-player %s, mpv up", __version__)
        content = pair = monitor = wifi = None
        try:
            content = content_mod.Content(cfg)
            content.start()
        except OSError as e:
            log.warning("content updates disabled (%s); playing local file only", e)
        if content:
            pair = pairing.Pairing(cfg, content)
            pair.start()
            heartbeat.Heartbeat(cfg, content).start()
            monitor = netman.Monitor(cfg["cms_url"]).start()
            if netman.available():
                wifi = netman.WifiSetup(pair.device_id, cfg["wifi_retry"], monitor).start()
            else:
                log.warning("nmcli not found: no wifi setup hotspot (dev machine?)")
        Agent(cfg, mpv, content, pair, monitor, wifi).loop()
    except MpvError as e:
        # Let systemd restart the whole agent (fresh mpv, fresh state) — simplest recovery.
        log.error("%s; exiting for restart", e)
        mpv.stop()
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
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load(a.config)
    if a.command == "reset":
        return reset(cfg)
    if a.video:
        cfg["video"] = a.video
    if a.epoch is not None:
        cfg["epoch"] = a.epoch
    run(cfg)


if __name__ == "__main__":
    main()
