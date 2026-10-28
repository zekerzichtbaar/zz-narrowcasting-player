"""What the CMS shows under "Details" on /schermen: hardware, network and health of this Pi, sent
with every heartbeat as `device` (docs/protocol.md section 4). Best effort: whatever can't be read
is None, nothing here ever raises.

Cost: /proc and /sys reads are microseconds; the fixed facts are read once, nmcli and vcgencmd
(a process each) at most once a minute (SLOW_EVERY), so the 30 s heartbeat stays cheap.

Self-check: python3 -m pi_player.deviceinfo
"""
import os
import platform
import socket
import subprocess
import time

SLOW_EVERY = 60.0


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _run(*args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def serial(cpuinfo):
    """The Pi serial from /proc/cpuinfo ("Serial : 10000000a1b2c3d4")."""
    for line in (cpuinfo or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Serial" and value.strip():
            return value.strip()
    return None


def os_name(os_release):
    for line in (os_release or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"') or None
    return None


def default_iface(route):
    """The interface of the default route in /proc/net/route (destination 00000000)."""
    for line in (route or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) > 1 and fields[1] == "00000000":
            return fields[0]
    return None


def wifi_now(nmcli_out):
    """(ssid, signal %) of the active wifi from `nmcli -t -f ACTIVE,SSID,SIGNAL device wifi`."""
    for line in (nmcli_out or "").splitlines():
        # SSIDs may hold ':' (escaped as '\\:' by nmcli -t); the signal is the last field.
        active, _, rest = line.partition(":")
        ssid, _, signal = rest.rpartition(":")
        if active == "yes":
            return (ssid.replace("\\:", ":") or None, int(signal) if signal.isdigit() else None)
    return None, None


def meminfo(text):
    """(total, available) bytes from /proc/meminfo."""
    values = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    return values.get("MemTotal"), values.get("MemAvailable")


def hdmi(drm="/sys/class/drm"):
    """{connected, mode} of the first HDMI connector: mode = its preferred (first) mode."""
    try:
        names = sorted(n for n in os.listdir(drm) if "HDMI" in n)
    except OSError:
        return None
    for name in names:
        status = (_read(os.path.join(drm, name, "status")) or "").strip()
        if status == "connected":
            modes = (_read(os.path.join(drm, name, "modes")) or "").split()
            return {"connected": True, "mode": modes[0] if modes else None}
    return {"connected": False, "mode": None} if names else None


def throttled(vcgencmd_out):
    """vcgencmd get_throttled -> '0x0' (0x0 = fine; bit 0 under-voltage now, bit 16 since boot)."""
    out = (vcgencmd_out or "").strip()
    return out.split("=", 1)[1] if out.startswith("throttled=") else None


class DeviceInfo:
    """`report()` for each heartbeat: fixed facts read once, slow ones cached SLOW_EVERY s."""

    def __init__(self, root="/", clock=time.monotonic):
        self.root, self.clock = root, clock
        p = self._path
        self.fixed = {
            "serial": serial(_read(p("proc/cpuinfo"))),
            "model": ((_read(p("proc/device-tree/model")) or "").strip("\x00\n ") or None),
            "os": os_name(_read(p("etc/os-release"))),
            "kernel": platform.release() or None,
            "hostname": socket.gethostname() or None,
        }
        self.slow, self.slow_at = {}, -1e9

    def _path(self, rel):
        return os.path.join(self.root, rel)

    def _slow(self):
        if self.clock() - self.slow_at >= SLOW_EVERY:
            ssid, signal = wifi_now(_run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi"))
            self.slow = {"ssid": ssid, "signal": signal,
                         "throttled": throttled(_run("vcgencmd", "get_throttled"))}
            self.slow_at = self.clock()
        return self.slow

    def report(self):
        p = self._path
        iface = default_iface(_read(p("proc/net/route")))
        temp = (_read(p("sys/class/thermal/thermal_zone0/temp")) or "").strip()
        uptime = (_read(p("proc/uptime")) or "").split()
        total, available = meminfo(_read(p("proc/meminfo")))
        try:
            load = round(os.getloadavg()[0], 2)
        except OSError:
            load = None
        return {
            **self.fixed,
            "iface": iface,
            "mac": ((_read(p(f"sys/class/net/{iface}/address")) or "").strip() or None) if iface else None,
            **self._slow(),
            "temp_c": round(int(temp) / 1000, 1) if temp.lstrip("-").isdigit() else None,
            "uptime_s": int(float(uptime[0])) if uptime else None,
            "mem_total": total,
            "mem_available": available,
            "load": load,
            "hdmi": hdmi(p("sys/class/drm")),
        }


def _selfcheck():
    import tempfile
    assert serial("processor\t: 0\nSerial\t\t: 10000000a1b2c3d4\nModel\t: Raspberry Pi 4") == "10000000a1b2c3d4"
    assert serial("") is None and serial(None) is None
    assert os_name('NAME="Debian"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n') == "Debian GNU/Linux 12 (bookworm)"
    route = "Iface\tDestination\tGateway\nwlan0\t0000A8C0\t00000000\nwlan0\t00000000\t0100A8C0\n"
    assert default_iface(route) == "wlan0" and default_iface("Iface\n") is None
    assert wifi_now("no:Buren:40\nyes:Kantine\\:gast:72\n") == ("Kantine:gast", 72)
    assert wifi_now("") == (None, None)
    assert meminfo("MemTotal:  3884000 kB\nMemFree: 1 kB\nMemAvailable:  2000000 kB\n") == (3884000 * 1024, 2000000 * 1024)
    assert throttled("throttled=0x50005\n") == "0x50005" and throttled(None) is None

    root = tempfile.mkdtemp()
    for rel, text in (("proc/cpuinfo", "Serial\t: 10000000feedbeef\n"),
                      ("proc/device-tree/model", "Raspberry Pi 4 Model B Rev 1.5\x00"),
                      ("etc/os-release", 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'),
                      ("proc/net/route", "Iface\tDestination\neth0\t00000000\n"),
                      ("sys/class/net/eth0/address", "dc:a6:32:00:00:01\n"),
                      ("sys/class/thermal/thermal_zone0/temp", "52608\n"),
                      ("proc/uptime", "86400.52 1000.0\n"),
                      ("proc/meminfo", "MemTotal: 1000 kB\nMemAvailable: 400 kB\n"),
                      ("sys/class/drm/card1-HDMI-A-1/status", "connected\n"),
                      ("sys/class/drm/card1-HDMI-A-1/modes", "1920x1080\n1280x720\n")):
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w") as f:
            f.write(text)
    t = [0.0]
    info = DeviceInfo(root, clock=lambda: t[0])
    r = info.report()
    assert r["serial"] == "10000000feedbeef" and r["model"] == "Raspberry Pi 4 Model B Rev 1.5", r
    assert r["iface"] == "eth0" and r["mac"] == "dc:a6:32:00:00:01" and r["temp_c"] == 52.6
    assert r["uptime_s"] == 86400 and r["mem_total"] == 1024000 and r["mem_available"] == 409600
    assert r["hdmi"] == {"connected": True, "mode": "1920x1080"}
    slow_at = info.slow_at
    t[0] += 30
    info.report()
    assert info.slow_at == slow_at  # nmcli/vcgencmd not again within a minute
    t[0] += SLOW_EVERY
    info.report()
    assert info.slow_at > slow_at
    empty = DeviceInfo(tempfile.mkdtemp()).report()  # nothing readable: all None, no exception
    assert empty["serial"] is None and empty["temp_c"] is None and empty["hdmi"] is None
    print("deviceinfo self-check OK")


if __name__ == "__main__":
    _selfcheck()
