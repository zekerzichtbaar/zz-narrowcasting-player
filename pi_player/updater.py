"""Player updates from the CMS ("Speler bijwerken", docs/protocol.md section 3.1 `update` and section 7).

Two halves, so a broken new release can never take the fallback down with it:

- Agent side (`Updater`, a thread in the running agent): sees the manifest's `update`, copies the
  running code to /opt/pi-player/prev/pi_player and starts the runner as its own transient systemd
  unit (`systemd-run --unit pi-player-update`): install.sh restarts pi-player.service, and the
  runner must outlive that.
- Runner (`python3 -m pi_player.updater apply VERSION URL`, imported FROM THAT BACKUP, i.e. the old
  code): download the tarball, unpack, run its install.sh with PI_PLAYER_VERSION, then wait for the
  new agent's first successful heartbeat (state_dir/alive.json). install.sh failing or no heartbeat
  of the new version within VERIFY_SECONDS = put the backup back and restart the old agent.

install.sh keeps /etc/pi-player/config.toml and /var/lib/pi-player (token, videos, state.json); this
module only writes update.json and alive.json there. Never reboots: install.sh only prints a hint
when the kernel cmdline changed, which then applies at the next reboot.

state_dir/update.json = {"version", "status": "running"|"done"|"failed", "from", "at", "reason"}.
A failed version is not tried again while the CMS keeps asking for it (the beheer then keeps showing
"Wordt bijgewerkt"); a different version is. To retry the same one: delete update.json.

Self-check: python3 -m pi_player.updater
"""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

from . import VERSION_RE, __version__

log = logging.getLogger("pi-player")

APP = "/opt/pi-player"
UNIT = "/etc/systemd/system/pi-player.service"
RUNNER_UNIT = "pi-player-update"
# ponytail: no own signing; the tarball comes over https from GitHub, and only from this repo, so
# a compromised CMS cannot hand the Pi other code. A fork can override `update_url_prefix`.
URL_PREFIX = "https://github.com/zekerzichtbaar/zz-narrowcasting-player/"
VERIFY_SECONDS = 300  # new version must heartbeat within this, else roll back
RUN_TIMEOUT = 45 * 60  # a "running" status older than this is a runner that died (e.g. a reboot)
MAX_TARBALL = 100 * 1024 * 1024
TICK = 10.0


class Paths:
    def __init__(self, app=APP, state="/var/lib/pi-player", unit=UNIT):
        self.app, self.state, self.unit = app, state, unit
        self.code = os.path.join(app, "pi_player")
        self.prev = os.path.join(app, "prev")  # prev/pi_player (+ prev/pi-player.service)
        self.status = os.path.join(state, "update.json")
        self.alive = os.path.join(state, "alive.json")


# ---------- pure helpers ----------

def valid_update(u, prefix=URL_PREFIX):
    """The manifest's `update` as {"version", "url"} if usable, else None."""
    if not isinstance(u, dict):
        return None
    v, url = u.get("version"), u.get("url")
    if not isinstance(v, str) or not VERSION_RE.match(v) or not isinstance(url, str) or not url.startswith(prefix):
        return None
    return {"version": v, "url": url}


def wants(update, current, status, now):
    """The version to install now, or None. `status` = update.json ({} if none)."""
    if not update or update["version"] == current:
        return None
    if status.get("status") == "running" and now - float(status.get("at") or 0) < RUN_TIMEOUT:
        return None  # one at a time
    if status.get("status") == "failed" and status.get("version") == update["version"]:
        return None  # rolled back once: not again for the same version
    return update["version"]


def alive_ok(alive, version, since):
    """Did the agent of `version` heartbeat successfully at or after `since`?"""
    return bool(alive) and alive.get("version") == version and float(alive.get("at") or 0) >= since


# ---------- files ----------

def read_json(path):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def mark_alive(state_dir, version=None):
    """Called by the heartbeat after its first 2xx: what the runner waits for."""
    try:
        write_json(os.path.join(state_dir, "alive.json"), {"version": version or __version__, "at": time.time()})
    except OSError as e:
        log.warning("writing alive.json failed: %s", e)


def set_status(paths, version, status, reason=None, frm=None):
    st = {"version": version, "status": status, "from": frm or __version__, "at": time.time(), "reason": reason}
    write_json(paths.status, st)
    log.info("update %s: %s%s", version, status, f" ({reason})" if reason else "")


def _replace_tree(src, dest):
    """dest becomes a copy of src (copy next to it, then swap)."""
    tmp = dest + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)


def backup(paths):
    """Running code (+ unit file) -> prev/. The runner is imported from there."""
    _replace_tree(paths.code, os.path.join(paths.prev, "pi_player"))
    unit = os.path.join(paths.prev, "pi-player.service")
    if os.path.exists(paths.unit):
        shutil.copyfile(paths.unit, unit)
    elif os.path.exists(unit):
        os.unlink(unit)


def fetch(url, dest, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r, open(dest, "wb") as f:
        n = 0
        for chunk in iter(lambda: r.read(1 << 20), b""):
            n += len(chunk)
            if n > MAX_TARBALL:
                raise ValueError("tarball too big")
            f.write(chunk)


def unpack(tarball, into):
    """Extract a GitHub tarball (one top dir) safely; returns that top dir. Only plain files and dirs,
    nothing outside `into`."""
    root = os.path.realpath(into)
    with tarfile.open(tarball, "r:gz") as t:
        members = []
        for m in t.getmembers():
            dest = os.path.realpath(os.path.join(root, m.name))
            if not dest.startswith(root + os.sep):
                raise ValueError(f"unsafe path in tarball: {m.name}")
            if m.isfile() or m.isdir():
                members.append(m)
        kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        t.extractall(root, members=members, **kw)
    tops = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    if len(tops) != 1:
        raise ValueError("tarball must hold one directory")
    src = os.path.join(root, tops[0])
    if not (os.path.isfile(os.path.join(src, "install.sh")) and os.path.isfile(os.path.join(src, "pi_player", "__init__.py"))):
        raise ValueError("not a pi-player release")
    return src


# ---------- runner (its own process, old code) ----------

def run_install(src, version):
    """The real install: the release's own install.sh (it restarts pi-player.service)."""
    env = {**os.environ, "PI_PLAYER_VERSION": version}
    r = subprocess.run(["bash", os.path.join(src, "install.sh")], env=env, timeout=RUN_TIMEOUT - 300)
    if r.returncode != 0:
        raise RuntimeError(f"install.sh exited {r.returncode}")


def restart_service():
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "restart", "pi-player.service"], check=False)


def rollback(paths, restart):
    prev = os.path.join(paths.prev, "pi_player")
    _replace_tree(prev, paths.code)
    unit = os.path.join(paths.prev, "pi-player.service")
    if os.path.exists(unit):
        shutil.copyfile(unit, paths.unit)
    restart()


def apply(paths, version, url, frm=None, install=run_install, restart=restart_service,
          verify=VERIFY_SECONDS, poll=5.0):
    """Install `version` from `url`; roll back unless it heartbeats in time. Returns True on success."""
    frm = frm or __version__
    set_status(paths, version, "running", frm=frm)
    work = tempfile.mkdtemp(prefix="pi-player-update-")
    try:
        try:
            tarball = os.path.join(work, "release.tar.gz")
            fetch(url, tarball)
            src = unpack(tarball, os.path.join(work, "src"))
        except Exception as e:  # nothing changed yet: just remember it failed
            set_status(paths, version, "failed", f"download: {e}", frm)
            return False
        since = time.time()
        try:
            install(src, version)
        except Exception as e:
            log.error("update %s: install failed (%s); rolling back to %s", version, e, frm)
            rollback(paths, restart)
            set_status(paths, version, "failed", f"install: {e}", frm)
            return False
        deadline = time.time() + verify
        while time.time() < deadline:
            if alive_ok(read_json(paths.alive), version, since):
                set_status(paths, version, "done", frm=frm)
                return True
            time.sleep(poll)
        log.error("update %s: no heartbeat within %s s; rolling back to %s", version, verify, frm)
        rollback(paths, restart)
        set_status(paths, version, "failed", f"no heartbeat within {verify} s", frm)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- agent side ----------

def launch_runner(paths, version, url):
    """Start the runner from the backup as a transient unit; it survives pi-player.service restarts."""
    subprocess.run(["systemd-run", f"--unit={RUNNER_UNIT}", "--collect", "--quiet",
                    f"--setenv=PYTHONPATH={paths.prev}", "--setenv=PYTHONUNBUFFERED=1",
                    "/usr/bin/python3", "-m", "pi_player.updater", "apply", version, url,
                    "--app", paths.app, "--state", paths.state, "--from", __version__],
                   check=True, timeout=30)


class Updater:
    """Background thread in the agent: start the runner when the CMS asks for another version."""

    def __init__(self, cfg, content, paths=None, launch=launch_runner):
        self.content = content
        self.prefix = cfg.get("update_url_prefix") or URL_PREFIX
        self.paths = paths or Paths(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), cfg["state_dir"])
        self.launch = launch

    def start(self):
        threading.Thread(target=self._loop, name="updater", daemon=True).start()
        return self

    def tick(self, now=None):
        """Returns the version it started, or None."""
        u = valid_update(self.content.update_request(), self.prefix)
        v = wants(u, __version__, read_json(self.paths.status), now or time.time())
        if not v:
            return None
        try:
            backup(self.paths)
            set_status(self.paths, v, "running")
            self.launch(self.paths, v, u["url"])
        except Exception as e:  # e.g. no systemd-run on a dev box: do not retry every tick
            set_status(self.paths, v, "failed", f"start: {e}")
            return None
        log.info("update to %s started (runner unit %s)", v, RUNNER_UNIT)
        return v

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:
                log.warning("updater: %s", e)
            time.sleep(TICK)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pi_player.updater")
    p.add_argument("command", choices=["apply"])
    p.add_argument("version")
    p.add_argument("url")
    p.add_argument("--app", default=APP)
    p.add_argument("--state", default="/var/lib/pi-player")
    p.add_argument("--from", dest="frm", default=None)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not VERSION_RE.match(a.version):
        sys.exit("bad version")
    sys.exit(0 if apply(Paths(a.app, a.state), a.version, a.url, a.frm) else 1)


# ---------- self-check ----------

def _selfcheck():
    import http.server
    import io

    # pure: what to install
    good = {"version": "v1.3.0", "url": URL_PREFIX + "archive/refs/tags/v1.3.0.tar.gz"}
    assert valid_update(good) == good
    assert valid_update(None) is None and valid_update("v1") is None
    assert valid_update({**good, "url": "https://evil.example/x.tar.gz"}) is None
    assert valid_update({**good, "url": "http://github.com/zekerzichtbaar/zz-narrowcasting-player/x"}) is None
    assert valid_update({**good, "version": "v1; rm -rf /"}) is None
    assert valid_update({**good, "extra": 1}) == good
    now = 10_000.0
    assert wants(good, "v1.2.0", {}, now) == "v1.3.0"
    assert wants(None, "v1.2.0", {}, now) is None
    assert wants(good, "v1.3.0", {}, now) is None  # already there
    assert wants(good, "v1.2.0", {"version": "v1.3.0", "status": "failed"}, now) is None
    assert wants(good, "v1.2.0", {"version": "v1.2.5", "status": "failed"}, now) == "v1.3.0"
    assert wants(good, "v1.2.0", {"version": "v1.3.0", "status": "running", "at": now - 60}, now) is None
    assert wants(good, "v1.2.0", {"version": "v1.3.0", "status": "running", "at": now - RUN_TIMEOUT - 1}, now) == "v1.3.0"
    assert wants(good, "v1.2.0", {"version": "v1.3.0", "status": "done"}, now) == "v1.3.0"
    assert alive_ok({"version": "v1.3.0", "at": 5}, "v1.3.0", 5)
    assert not alive_ok({"version": "v1.3.0", "at": 4}, "v1.3.0", 5)  # the old process's mark
    assert not alive_ok({"version": "v1.2.0", "at": 9}, "v1.3.0", 5) and not alive_ok({}, "v1.3.0", 0)

    root = tempfile.mkdtemp()
    try:
        # unpack: refuses paths outside, skips links, needs one release dir
        def tgz(path, files, links=()):
            with tarfile.open(path, "w:gz") as t:
                for name, data in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o755 if name.endswith(".sh") else 0o644
                    t.addfile(info, io.BytesIO(data))
                for name, target in links:
                    info = tarfile.TarInfo(name)
                    info.type, info.linkname = tarfile.SYMTYPE, target
                    t.addfile(info)

        bad = os.path.join(root, "bad.tgz")
        tgz(bad, {"r/install.sh": b"", "r/pi_player/__init__.py": b"", "../evil": b"x"})
        try:
            unpack(bad, os.path.join(root, "u1"))
            raise AssertionError("expected unsafe path")
        except ValueError as e:
            assert "unsafe" in str(e)
        tgz(bad, {"r/README": b"x"})
        try:
            unpack(bad, os.path.join(root, "u2"))
            raise AssertionError("expected not a release")
        except ValueError:
            pass
        tgz(bad, {"r/install.sh": b"", "r/pi_player/__init__.py": b""}, links=[("r/etc", "/etc")])
        src = unpack(bad, os.path.join(root, "u3"))
        assert src.endswith("/r") and not os.path.lexists(os.path.join(src, "etc"))

        # a fake Pi under root: app (code v1.2.0), unit file, state (token, videos), config
        app, state_dir, etc = (os.path.join(root, d) for d in ("opt", "state", "etc"))
        paths = Paths(app, state_dir, os.path.join(etc, "pi-player.service"))
        os.makedirs(paths.code)
        os.makedirs(state_dir)
        os.makedirs(etc)
        open(os.path.join(paths.code, "__init__.py"), "w").write("# old\n")
        open(os.path.join(paths.code, "VERSION"), "w").write("v1.2.0\n")
        open(paths.unit, "w").write("old unit\n")
        open(os.path.join(etc, "config.toml"), "w").write('cms_url = "https://cms"\n')
        for name, data in (("token", "tok\n"), ("current.mp4", "video"), ("state.json", '{"current": null}')):
            open(os.path.join(state_dir, name), "w").write(data)
        keep = [os.path.join(state_dir, n) for n in ("token", "current.mp4", "state.json")]
        keep = {p: open(p).read() for p in keep + [os.path.join(etc, "config.toml")]}

        # releases served over http (the real thing is https from GitHub; prefix checked by the agent)
        # install.sh stub = what the real one does to our files: new code + VERSION + unit, restart
        install_sh = (b'set -e\nAPP="$PI_PLAYER_TEST_APP"\nSRC="$(cd "$(dirname "$0")" && pwd)"\n'
                      b'[ "$PI_PLAYER_TEST_FAIL" = 1 ] && { rm -rf "$APP/pi_player"; exit 3; }\n'
                      b'rm -rf "$APP/pi_player"; cp -r "$SRC/pi_player" "$APP/pi_player"\n'
                      b'printf "%s\\n" "$PI_PLAYER_VERSION" > "$APP/pi_player/VERSION"\n'
                      b'echo "new unit" > "$PI_PLAYER_TEST_UNIT"\n')
        www = os.path.join(root, "www")
        os.makedirs(www)
        tgz(os.path.join(www, "v1.3.0.tar.gz"), {"zz-narrowcasting-player-1.3.0/install.sh": install_sh,
                                                 "zz-narrowcasting-player-1.3.0/pi_player/__init__.py": b"# new\n"})

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=www, **k)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/v1.3.0.tar.gz"
        restarts = []

        def install(fail=False):
            def run(src, version):
                env = {**os.environ, "PI_PLAYER_VERSION": version, "PI_PLAYER_TEST_APP": app,
                       "PI_PLAYER_TEST_UNIT": paths.unit, "PI_PLAYER_TEST_FAIL": "1" if fail else "0"}
                r = subprocess.run(["bash", os.path.join(src, "install.sh")], env=env)
                if r.returncode:
                    raise RuntimeError(f"install.sh exited {r.returncode}")
                restarts.append("install")
            return run

        def code_version():
            return open(os.path.join(paths.code, "VERSION")).read().strip()

        def kept():
            return all(open(p).read() == v for p, v in keep.items())

        # agent side: an update request starts the runner once, after backing up the running code
        class FakeContent:
            req = None

            def update_request(self):
                return self.req

        launched = []
        fc = FakeContent()
        up = Updater({"state_dir": state_dir, "update_url_prefix": "http://127.0.0.1:"}, fc, paths,
                     lambda p, v, u: launched.append((v, u)))
        assert up.tick() is None and launched == []
        fc.req = {"version": "v1.3.0", "url": url}
        assert up.tick() == "v1.3.0" and launched == [("v1.3.0", url)]
        assert open(os.path.join(paths.prev, "pi_player", "VERSION")).read().strip() == "v1.2.0"
        assert open(os.path.join(paths.prev, "pi-player.service")).read() == "old unit\n"
        assert read_json(paths.status)["status"] == "running"
        assert up.tick() is None and len(launched) == 1  # running: not twice
        assert Updater({"state_dir": state_dir}, fc, paths, lambda *a: None).tick() is None  # wrong prefix

        # 1. success: the new agent heartbeats -> done; token, videos, state, config untouched
        def restart_new():
            restarts.append("restart")

        inst = install()

        def install_and_heartbeat(src, version):
            inst(src, version)
            mark_alive(state_dir, version)  # what the new agent's heartbeat does after its first 204

        assert apply(paths, "v1.3.0", url, "v1.2.0", install_and_heartbeat, restart_new, verify=5, poll=0.05)
        assert code_version() == "v1.3.0" and open(paths.unit).read() == "new unit\n"
        assert read_json(paths.status)["status"] == "done" and restarts == ["install"] and kept()

        # reset to v1.2.0 for the failure cases
        def reset_old():
            _replace_tree(os.path.join(paths.prev, "pi_player"), paths.code)
            open(paths.unit, "w").write("old unit\n")
            for p in (paths.status, paths.alive):
                if os.path.exists(p):
                    os.unlink(p)
            restarts.clear()

        # 2. new version starts but never heartbeats (crash loop, no network to the CMS): rolled back
        reset_old()
        mark_alive(state_dir, "v1.2.0")  # the old agent's mark does not count
        t0 = time.time()
        assert not apply(paths, "v1.3.0", url, "v1.2.0", install(), lambda: restarts.append("rollback"),
                         verify=0.3, poll=0.05)
        assert time.time() - t0 < 5
        assert code_version() == "v1.2.0" and open(paths.unit).read() == "old unit\n"
        assert restarts == ["install", "rollback"] and kept()
        st = read_json(paths.status)
        assert st["status"] == "failed" and st["version"] == "v1.3.0" and "heartbeat" in st["reason"]
        assert st["from"] == "v1.2.0"
        # ... and the agent does not try that version again, but would a newer one
        fc.req = {"version": "v1.3.0", "url": url}
        assert up.tick() is None
        fc.req = {"version": "v1.3.1", "url": url}
        launched.clear()
        assert up.tick() == "v1.3.1" and launched == [("v1.3.1", url)]

        # 3. install.sh fails halfway (code dir gone): rolled back right away
        reset_old()
        assert not apply(paths, "v1.3.0", url, "v1.2.0", install(fail=True), lambda: restarts.append("rollback"),
                         verify=5, poll=0.05)
        assert code_version() == "v1.2.0" and restarts == ["rollback"] and kept()
        assert read_json(paths.status)["reason"].startswith("install:")

        # 4. download fails (404): nothing touched, nothing restarted
        reset_old()
        assert not apply(paths, "v9.9.9", url.replace("v1.3.0", "v9.9.9"), "v1.2.0", install(),
                         lambda: restarts.append("rollback"), verify=5, poll=0.05)
        assert code_version() == "v1.2.0" and restarts == [] and kept()
        assert read_json(paths.status)["reason"].startswith("download:")

        # 5. launching the runner fails (no systemd-run): failed, not retried every tick
        reset_old()

        def no_systemd(*a):
            raise FileNotFoundError("systemd-run")

        up2 = Updater({"state_dir": state_dir, "update_url_prefix": "http://127.0.0.1:"}, fc, paths, no_systemd)
        fc.req = {"version": "v1.3.0", "url": url}
        assert up2.tick() is None and read_json(paths.status)["status"] == "failed" and up2.tick() is None
        srv.shutdown()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("updater self-check OK")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        _selfcheck()
