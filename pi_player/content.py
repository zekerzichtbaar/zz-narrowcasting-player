"""Content updates: manifest fetch, verified download, atomic swap, activation timing.

Files in state_dir: current.mp4 (on screen), next.mp4 (verified, waiting), next.mp4.part (downloading),
state.json ({"current": manifest|null, "pending": manifest|null, "screen": {id,name,group_id}|null,
"rotation": 0|90|270 once a manifest carried one}).
Self-check: python3 -m pi_player.content
"""
import hashlib
import json
import logging
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .gst import to_rotation

log = logging.getLogger("pi-player")


class Unauthorized(Exception):
    """CMS answered 401: token revoked."""


# ---------- pure helpers ----------

def switch_time(manifest, ready_at, have_current=True):
    """When to put a verified download on screen.

    At activate_at if ready by then; if ready later, at the next loop boundary of the new video
    (k*duration since 1970) so it starts at frame 0 on every screen. Nothing on screen -> right away.
    The manifest's epoch is ignored, see play_synced.
    """
    if not have_current:
        return ready_at
    at = float(manifest.get("activate_at") or 0)
    if ready_at <= at:
        return at
    dur = float(manifest["duration"])
    return math.ceil(ready_at / dur) * dur


def identify_osd(name, until, now):
    """"Scherm identificeren" name card: (text, duration_ms) while until (Unix s) lies
    ahead and there is a name, else None."""
    ms = int((float(until or 0) - now) * 1000)
    return (name, ms) if name and ms > 0 else None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file(a, b):
    return bool(a.get("sha256")) and a.get("sha256", "").lower() == b.get("sha256", "").lower()


def _same_host(a, b):
    pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


# ---------- I/O ----------

def fetch_manifest(cms_url, token, timeout=15):
    """Return the manifest dict, None for 204 (no content). A 200 with version null (nothing to play
    while identifying) comes back as is. Raises Unauthorized on 401."""
    req = urllib.request.Request(cms_url.rstrip("/") + "/api/devices/manifest",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 204:
                return None
            m = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise Unauthorized() from e
        raise
    if m.get("url"):
        m["url"] = urllib.parse.urljoin(cms_url.rstrip("/") + "/", m["url"])
    return m


def download(manifest, dest, token=None, cms_url="", timeout=30, on_fail=None):
    """Download to dest.part, verify sha256, os.replace to dest. Returns True on success.

    On any failure the .part is removed and dest (and current.mp4) are left untouched, and
    on_fail(version, reason) is called.
    """
    part = dest + ".part"
    headers = {}
    if token and cms_url and _same_host(manifest["url"], cms_url):
        headers["Authorization"] = f"Bearer {token}"
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(urllib.request.Request(manifest["url"], headers=headers),
                                    timeout=timeout) as r, open(part, "wb") as f:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                h.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        if h.hexdigest() != manifest["sha256"].lower():
            raise ValueError("sha256 mismatch")
        os.replace(part, dest)
        return True
    except Exception as e:  # network, disk, mismatch: all mean "keep the old video"
        log.warning("download of %s failed: %s", manifest.get("version"), e)
        if on_fail:
            on_fail(manifest.get("version"), str(e))
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        return False


def copy_file(src, dest):
    """Copy via dest.part + os.replace (a real copy: a hard link would make the later
    os.replace(next, current) a no-op). Returns True on success."""
    part = dest + ".part"
    try:
        shutil.copyfile(src, part)
        os.replace(part, dest)
        return True
    except OSError as e:
        log.warning("copying %s failed: %s", src, e)
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        return False


def reset(state_dir):
    """`pi-player reset`: forget pairing and content -> first-boot flow. Returns removed paths."""
    gone = []
    for name in ("token", "token.tmp", "state.json", "current.mp4", "next.mp4", "next.mp4.part"):
        p = os.path.join(state_dir, name)
        try:
            os.unlink(p)
            gone.append(p)
        except FileNotFoundError:
            pass
    return gone


def load_state(state_dir):
    try:
        with open(os.path.join(state_dir, "state.json")) as f:
            return {"current": None, "pending": None, "screen": None, **json.load(f)}
    except (OSError, ValueError):
        return {"current": None, "pending": None, "screen": None}


def save_state(state_dir, st):
    path = os.path.join(state_dir, "state.json")
    with open(path + ".tmp", "w") as f:
        json.dump(st, f, indent=1)
    os.replace(path + ".tmp", path)


# ---------- runtime ----------

class Content:
    """Owns state.json and the video files. A background thread fetches + downloads; the play loop
    calls due() and activate() from its own thread."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg["state_dir"]
        self.current = os.path.join(self.dir, "current.mp4")
        self.next = os.path.join(self.dir, "next.mp4")
        self.lock = threading.Lock()
        self.wake = threading.Event()
        os.makedirs(self.dir, exist_ok=True)
        self.state = load_state(self.dir)
        self.switch_at = None  # set once next.mp4 is verified and waiting
        self.identify = 0  # identify_until (Unix s) of the last manifest; not persisted
        self.update = None  # `update` of the last manifest ("Speler bijwerken"); not persisted
        self.wifi_setup = None  # wifi_setup of the last manifest ("Wifi opnieuw instellen", 0 = stop); not persisted
        p = self.state["pending"]
        if p and os.path.exists(self.next):  # survived a restart: verified earlier
            self.switch_at = switch_time(p, time.time(), self.has_current())
        elif p:
            self.state["pending"] = None

    def token(self):
        try:
            with open(os.path.join(self.dir, "token")) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def forget_token(self):
        """Unpaired/revoked: delete the token so fetching and the heartbeat stop."""
        try:
            os.unlink(os.path.join(self.dir, "token"))
        except FileNotFoundError:
            pass

    def set_screen(self, screen):
        """Screen name/group from the pairing claim or a manifest."""
        with self.lock:
            if screen != self.state.get("screen"):
                self.state["screen"] = screen
                save_state(self.dir, self.state)

    def screen_name(self):
        with self.lock:
            return (self.state.get("screen") or {}).get("name")

    def clear(self):
        """Manifest 204: nothing published for this screen (any more) -> drop all video."""
        with self.lock:
            if not (self.state["current"] or self.state["pending"] or self.has_current()):
                return
            log.info("manifest: no content published; removing local video")
            for p in (self.current, self.next):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
            self.state["current"], self.state["pending"], self.switch_at = None, None, None
            save_state(self.dir, self.state)

    def update_request(self):
        """The manifest's raw `update` (validated by updater.valid_update), None when not asked."""
        with self.lock:
            return self.update

    def identify_until(self):
        with self.lock:
            return self.identify

    def wifi_setup_request(self):
        """When the CMS asked "Wifi opnieuw instellen" (Unix s, an id for the request), 0 when it
        asked to stop ("Wifi-scherm stoppen"), None when neither."""
        with self.lock:
            return self.wifi_setup

    def rotation(self):
        """How the panel hangs (0, 90, 270 clockwise), from the last manifest that said; kept in
        state.json so a restart without network still turns the picture the right way."""
        with self.lock:
            return to_rotation(self.state.get("rotation"))

    def set_rotation(self, value):
        r = to_rotation(value)
        with self.lock:
            if r != to_rotation(self.state.get("rotation")):
                log.info("rotation %s -> %s", self.state.get("rotation", 0), r)
                self.state["rotation"] = r
                save_state(self.dir, self.state)

    def has_current(self):
        return os.path.exists(self.current)

    def current_manifest(self):
        with self.lock:
            return self.state["current"]

    def pending_version(self):
        with self.lock:
            p = self.state["pending"]
            return p and p["version"]

    def due(self, now):
        """Seconds until the pending switch (<= 0 means switch now), or None if nothing pending."""
        with self.lock:
            return None if self.switch_at is None else self.switch_at - now

    def activate(self):
        """next.mp4 -> current.mp4 (atomic). Returns the now-current manifest (None if a clear() won)."""
        with self.lock:
            if self.switch_at is None or not os.path.exists(self.next):
                self.switch_at = None
                return None
            os.replace(self.next, self.current)
            self.state["current"], self.state["pending"] = self.state["pending"], None
            self.switch_at = None
            save_state(self.dir, self.state)
            return self.state["current"]

    def check(self):
        """One manifest fetch + download if needed. Called from the background thread.

        `version` is compared for equality only. A new version with the same file (sha256) as
        the one waiting or on screen is not downloaded again: the CMS makes a new version for a
        new group moment (activate_at/epoch) without a new file."""
        token = self.token()
        if not token:
            return
        m = fetch_manifest(self.cfg["cms_url"], token)
        with self.lock:  # identifying and updates are outside version: take them from every answer
            self.identify = float((m or {}).get("identify_until") or 0)
            self.update = (m or {}).get("update")
            w = (m or {}).get("wifi_setup")
            self.wifi_setup = w if isinstance(w, int) and not isinstance(w, bool) and w >= 0 else None
        if m and m.get("screen"):
            self.set_screen(m["screen"])
        if m and "rotation" in m:  # outside version too: turning needs no download, also when version null
            self.set_rotation(m["rotation"])
        if m is None or m.get("version") is None:
            self.clear()
            return
        with self.lock:
            cur, pend = self.state["current"], self.state["pending"]
        if cur and cur["version"] == m["version"] and self.has_current():
            if pend:  # CMS rolled back to what we're already showing: drop the pending one
                with self.lock:
                    self.state["pending"], self.switch_at = None, None
                    save_state(self.dir, self.state)
            return
        if pend and _same_file(pend, m) and os.path.exists(self.next):
            if pend != m:  # new version/activation for the file that is already waiting
                with self.lock:
                    self.state["pending"] = m
                    self.switch_at = switch_time(m, time.time(), self.has_current())
                    save_state(self.dir, self.state)
                log.info("version %s (same file as %s), switching at %.3f",
                         m["version"], pend["version"], self.switch_at)
            return
        with self.lock:  # a newer version supersedes a waiting one
            self.switch_at = None
            self.state["pending"] = None
        if cur and _same_file(cur, m) and self.has_current():
            # Same file as on screen, new clock (e.g. the group moment): reuse it, switch on time.
            if not copy_file(self.current, self.next):
                return
            log.info("version %s is the file on screen (%s); no download", m["version"], cur["version"])
        else:
            log.info("new version %s, downloading %s", m["version"], m["url"])
            if not download(m, self.next, token, self.cfg["cms_url"]):
                return
        now = time.time()
        with self.lock:
            self.state["pending"] = m
            self.switch_at = switch_time(m, now, self.has_current())
            save_state(self.dir, self.state)
        log.info("version %s ready, switching at %.3f (in %.1f s)",
                 m["version"], self.switch_at, self.switch_at - now)

    def start(self):
        threading.Thread(target=self._loop, name="content", daemon=True).start()

    def poke(self):
        """Fetch the manifest now (e.g. right after pairing)."""
        self.wake.set()

    def _loop(self):
        while True:
            try:
                self.check()
            except Unauthorized:
                log.warning("manifest: 401, token revoked; deleting token")
                self.forget_token()
            except Exception as e:
                log.warning("manifest fetch failed: %s", e)
            self.wake.wait(float(self.cfg["manifest_poll"]))
            self.wake.clear()


# ---------- self-check ----------

def _selfcheck():
    import http.server
    import shutil
    import tempfile

    # activation timing
    m = {"activate_at": 1000.0, "epoch": 0, "duration": 60.0}
    assert switch_time(m, 900.0) == 1000.0               # ready early -> exactly activate_at
    assert switch_time(m, 1000.0) == 1000.0
    assert switch_time(m, 1000.5) == 1020.0              # late -> next loop boundary (17*60)
    assert switch_time(m, 1020.0) == 1020.0              # late, exactly on a boundary
    assert switch_time({**m, "epoch": 5}, 1001.0) == 1020.0  # the CMS epoch is ignored
    assert switch_time(m, 900.0, have_current=False) == 900.0  # nothing on screen -> now
    # late switches land on a boundary for every screen, whenever each one finished
    assert switch_time(m, 1003.0) == switch_time(m, 1019.9)

    # identify overlay: name + ms left while ahead, nothing once past or without a name
    assert identify_osd("Kantine", 1060.0, 1000.0) == ("Kantine", 60000)
    assert identify_osd("Kantine", 1000.0, 1000.0) is None
    assert identify_osd("Kantine", 0, 1000.0) is None and identify_osd("Kantine", None, 1000.0) is None
    assert identify_osd(None, 1060.0, 1000.0) is None

    tmp = tempfile.mkdtemp(prefix="pi-player-content-")
    try:
        good, bad = b"good video bytes" * 1000, b"tampered"
        open(os.path.join(tmp, "a.mp4"), "wb").write(good)
        open(os.path.join(tmp, "b.mp4"), "wb").write(bad)

        hits = []

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=tmp, **k)

            def do_GET(self):
                hits.append(self.path)
                super().do_GET()

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}/"
        sha_good = hashlib.sha256(good).hexdigest()
        cur, nxt = os.path.join(tmp, "current.mp4"), os.path.join(tmp, "next.mp4")
        open(cur, "wb").write(b"old video")

        # sha mismatch rejected: no next.mp4, no .part, current untouched
        fails = []
        assert not download({"url": base + "b.mp4", "sha256": sha_good, "version": "v2"}, nxt,
                            on_fail=lambda v, why: fails.append((v, why)))
        assert fails == [("v2", "sha256 mismatch")]
        assert not os.path.exists(nxt) and not os.path.exists(nxt + ".part")
        assert open(cur, "rb").read() == b"old video"
        # failed download (404 / dead host) keeps the old file
        assert not download({"url": base + "missing.mp4", "sha256": sha_good, "version": "v2"}, nxt)
        assert not download({"url": "http://127.0.0.1:1/x.mp4", "sha256": sha_good, "version": "v2"}, nxt)
        assert not os.path.exists(nxt) and open(cur, "rb").read() == b"old video"
        # good download lands in next.mp4 only after verification
        assert download({"url": base + "a.mp4", "sha256": sha_good.upper(), "version": "v2"}, nxt)
        assert open(nxt, "rb").read() == good and not os.path.exists(nxt + ".part")
        assert open(cur, "rb").read() == b"old video"

        # atomic swap via Content.activate + state.json round trip
        c = Content({"state_dir": tmp, "cms_url": base, "manifest_poll": 30})
        man = {"version": "v2", "duration": 60.0, "epoch": 0, "activate_at": 0}
        c.state["pending"], c.switch_at = man, 0.0
        assert c.due(10.0) <= 0
        assert c.activate() == man
        assert open(cur, "rb").read() == good and not os.path.exists(nxt)
        assert load_state(tmp) == {"current": man, "pending": None, "screen": None}
        assert c.activate() is None  # nothing pending: no-op
        c.set_screen({"id": "s", "name": "Kantine"})
        assert c.screen_name() == "Kantine" and load_state(tmp)["screen"]["name"] == "Kantine"
        c.clear()
        assert not c.has_current() and load_state(tmp)["current"] is None and c.screen_name() == "Kantine"
        open(cur, "wb").write(good)
        assert c.due(10.0) is None
        open(os.path.join(tmp, "token"), "w").write("t\n")
        assert c.token() == "t"
        c.forget_token()
        c.forget_token()  # idempotent
        assert c.token() is None
        open(os.path.join(tmp, "token"), "w").write("t\n")
        assert os.path.join(tmp, "token") in reset(tmp)
        assert not any(os.path.exists(os.path.join(tmp, n)) for n in ("token", "state.json", "current.mp4"))

        # check(): manifest poll walk-through (fetch_manifest stubbed, files from the test server)
        global fetch_manifest
        real_fetch, served = fetch_manifest, {}
        fetch_manifest = lambda cms_url, token: served["m"]
        try:
            open(os.path.join(tmp, "token"), "w").write("t\n")
            c = Content({"state_dir": tmp, "cms_url": base, "manifest_poll": 30})
            other = b"other video" * 1000
            open(os.path.join(tmp, "c.mp4"), "wb").write(other)
            wait = {"version": "7-4102444800-abc", "url": base + "a.mp4", "sha256": sha_good,
                    "duration": 60.0, "epoch": 0, "activate_at": 4102444800}
            # waiting for the group with nothing on screen: download, show right away
            served["m"], hits[:] = wait, []
            c.check()
            assert hits == ["/a.mp4"] and c.pending_version() == wait["version"] and c.due(time.time()) <= 0
            assert c.activate() == wait and open(cur, "rb").read() == good
            c.check()  # same version again: nothing
            assert hits == ["/a.mp4"] and c.pending_version() is None and c.identify_until() == 0
            # identifying: same version, nothing downloaded, identify_until taken over
            served["m"] = {**wait, "identify_until": 4102444860}
            c.check()
            assert hits == ["/a.mp4"] and c.pending_version() is None and c.identify_until() == 4102444860
            served["m"] = wait
            c.check()
            assert c.identify_until() == 0 and c.rotation() == 0 and c.update_request() is None
            # update request ("Speler bijwerken"): same version, nothing downloaded or switched
            upd = {"version": "v9.0.0", "url": "https://github.com/zekerzichtbaar/zz-narrowcasting-player/x.tar.gz"}
            served["m"] = {**wait, "update": upd}
            c.check()
            assert hits == ["/a.mp4"] and c.pending_version() is None and c.update_request() == upd
            assert c.current_manifest() == wait  # not stored with the content
            served["m"] = wait  # asked update done (or an older CMS without the field)
            c.check()
            assert c.update_request() is None
            # "Wifi opnieuw instellen": same version, nothing downloaded; junk = not asked
            served["m"] = {**wait, "wifi_setup": 1767225600}
            c.check()
            assert hits == ["/a.mp4"] and c.pending_version() is None and c.wifi_setup_request() == 1767225600
            served["m"] = {**wait, "wifi_setup": 0}  # stop
            c.check()
            assert c.wifi_setup_request() == 0
            served["m"] = {**wait, "wifi_setup": "x"}
            c.check()
            assert c.wifi_setup_request() is None
            # rotation: same version, nothing downloaded, taken over and kept in state.json
            served["m"] = {**wait, "rotation": 90, "orientation": "portrait"}
            c.check()
            assert hits == ["/a.mp4"] and c.pending_version() is None and c.rotation() == 90
            assert load_state(tmp)["rotation"] == 90
            assert Content({"state_dir": tmp, "cms_url": base, "manifest_poll": 30}).rotation() == 90
            served["m"] = {**wait, "rotation": "bogus"}
            c.check()
            assert c.rotation() == 0
            served["m"] = {**wait, "rotation": 270}
            c.check()
            served["m"] = wait  # an older CMS without the field: keep what we had
            c.check()
            assert c.rotation() == 270
            # group moment: new version, same file -> no download, switch exactly at activate_at
            t = time.time() + 45
            moment = {**wait, "version": f"7-{int(t)}-abc", "activate_at": t, "epoch": t}
            served["m"] = moment
            c.check()
            assert hits == ["/a.mp4"] and open(nxt, "rb").read() == good
            assert c.pending_version() == moment["version"] and c.switch_at == t
            assert load_state(tmp)["pending"] == moment
            # moment moved (same file waiting in next.mp4): only the switch time changes
            moved = {**moment, "version": f"7-{int(t) + 10}-abc", "activate_at": t + 10, "epoch": t + 10}
            served["m"] = moved
            c.check()
            assert hits == ["/a.mp4"] and c.pending_version() == moved["version"] and c.switch_at == t + 10
            # really new file: downloaded, supersedes the waiting one
            new = {**moved, "version": "8-0-def", "url": base + "c.mp4",
                   "sha256": hashlib.sha256(other).hexdigest(), "activate_at": 0, "epoch": 0}
            served["m"] = new
            c.check()
            assert hits == ["/a.mp4", "/c.mp4"] and c.pending_version() == "8-0-def"
            assert open(nxt, "rb").read() == other
            # CMS rolls back to what is on screen: pending dropped
            served["m"] = wait
            c.check()
            assert c.pending_version() is None and c.due(time.time()) is None
            # nothing to play while identifying: like a 204, but the name still shows
            served["m"] = {"version": None, "identify_until": 4102444860, "rotation": 90,
                           "screen": {"id": 1, "name": "Kantine", "group_id": None}}
            c.check()
            assert c.rotation() == 90
            assert not c.has_current() and c.current_manifest() is None and c.pending_version() is None
            assert c.identify_until() == 4102444860 and c.screen_name() == "Kantine"
            # 204: everything gone
            served["m"] = None
            c.check()
            assert not c.has_current() and c.current_manifest() is None and c.identify_until() == 0
            assert c.rotation() == 90  # a 204 has no body: the panel still hangs the same way
        finally:
            fetch_manifest = real_fetch
        srv.shutdown()

        # rotation over the wire, live against mock_cms.py: also with nothing published (version null)
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import mock_cms
        mock_cms.log = lambda *a: None
        mock_cms.ring = lambda *a, **k: None  # no bel here (bell.py's self-check covers it)
        cms = mock_cms.make_server(mock_cms.parse_args(["--bind", "127.0.0.1", "--port", "0", "--media", tmp]))
        threading.Thread(target=cms.serve_forever, daemon=True).start()
        cms_url = f"http://127.0.0.1:{cms.server_port}"
        mock_cms.STATE.update(manifest=None, identify_until=0, rotation=0)
        assert fetch_manifest(cms_url, "devtoken") is None  # 204
        open(os.path.join(tmp, "token"), "w").write("devtoken\n")
        c = Content({"state_dir": tmp, "cms_url": cms_url, "manifest_poll": 30})
        post = lambda path: urllib.request.urlopen(urllib.request.Request(cms_url + path, method="POST"), timeout=5).read()
        post("/admin/publish?file=a.mp4&delay=0&duration=60&orientation=portrait")
        c.check()
        assert c.rotation() == 0 and c.pending_version()
        assert c.state["pending"]["orientation"] == "portrait"
        post("/admin/rotate?rotation=270")
        c.check()
        assert c.rotation() == 270
        post("/admin/unpublish")
        post("/admin/rotate?rotation=90")
        post("/admin/identify?seconds=60")
        c.check()
        assert c.rotation() == 90 and c.identify_until() > time.time()
        # "Speler bijwerken" over the wire: in the version-null body too, gone once the heartbeat reports it
        from .updater import valid_update
        mock_cms.STATE.update(identify_until=0)
        post("/admin/update?version=v9.0.0")
        c.check()
        assert valid_update(c.update_request()) == {
            "version": "v9.0.0", "url": "https://github.com/zekerzichtbaar/zz-narrowcasting-player/archive/refs/tags/v9.0.0.tar.gz",
            "attempt": str(mock_cms.STATE["update"]["attempt"])}
        assert not c.has_current() and c.pending_version() is None
        hb = urllib.request.Request(cms_url + "/api/devices/heartbeat", method="POST", data=b'{"agent_version": "v9.0.0"}',
                                    headers={"Authorization": "Bearer devtoken", "Content-Type": "application/json"})
        urllib.request.urlopen(hb, timeout=5).read()
        c.check()
        assert c.update_request() is None
        cms.shutdown()
    finally:
        shutil.rmtree(tmp)
    print("content self-check OK")


if __name__ == "__main__":
    _selfcheck()
