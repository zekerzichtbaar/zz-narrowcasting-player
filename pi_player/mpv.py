"""Launch mpv and talk to it over its JSON IPC socket (stdlib only)."""
import json
import os
import socket
import subprocess
import sys
import time

# Pi: draw straight to the HDMI output via DRM/KMS, no desktop needed.
LINUX_ARGS = ["--vo=gpu", "--gpu-context=drm", "--hwdec=auto"]

BASE_ARGS = [
    "--idle=yes", "--fullscreen", "--loop-file=inf", "--mute=yes", "--no-audio",
    "--no-osc", "--osd-level=0", "--no-input-default-bindings", "--input-vo-keyboard=no",
    "--cursor-autohide=always", "--really-quiet", "--no-terminal",
    "--image-display-duration=inf", "--keep-open=yes",
]


class MpvError(Exception):
    pass


class Mpv:
    def __init__(self, socket_path, mpv_bin="mpv", extra_args=()):
        self.socket_path = socket_path
        self.mpv_bin = mpv_bin
        self.extra_args = list(extra_args)
        self.proc = None
        self.sock = None
        self._buf = b""
        self._rid = 0

    def start(self, timeout=10):
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        args = [self.mpv_bin, *BASE_ARGS, *(LINUX_ARGS if sys.platform.startswith("linux") else []),
                f"--input-ipc-server={self.socket_path}", *self.extra_args]
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise MpvError(f"mpv exited with {self.proc.returncode}")
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.socket_path)
                s.settimeout(2)
                self.sock = s
                return
            except OSError:
                s.close()
                time.sleep(0.1)
        raise MpvError("mpv IPC socket did not appear")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def command(self, *cmd):
        """Send one command, return its `data`. Events arriving in between are dropped."""
        self._rid += 1
        rid = self._rid
        try:
            self.sock.sendall(json.dumps({"command": list(cmd), "request_id": rid}).encode() + b"\n")
            while True:
                while b"\n" not in self._buf:
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        raise MpvError("mpv IPC socket closed")
                    self._buf += chunk
                line, self._buf = self._buf.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("request_id") != rid:
                    continue  # event or stale reply
                if msg.get("error") != "success":
                    raise MpvError(f"{cmd}: {msg.get('error')}")
                return msg.get("data")
        except (OSError, ValueError) as e:
            raise MpvError(str(e)) from e

    def get(self, prop):
        return self.command("get_property", prop)

    def set(self, prop, value):
        return self.command("set_property", prop, value)

    def time_pos(self):
        return self.get("time-pos")

    def duration(self):
        return self.get("duration")

    def set_speed(self, x):
        return self.set("speed", x)

    def seek(self, t):
        return self.command("seek", t, "absolute+exact")

    def loadfile(self, path):
        return self.command("loadfile", path, "replace")
