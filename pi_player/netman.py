"""NetworkManager (nmcli) wrapper + wifi-setup controller (hotspot, scan, connect, retry).

Everything we create in NM is named `pi-player-*`, so `pi-player reset` can remove it again.
Pi 4 radio can't scan while it is an access point: the scan list is cached before the hotspot
starts, and a rescan drops the AP briefly.

Self-check: python3 -m pi_player.netman
"""
import logging
import queue
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse

log = logging.getLogger("pi-player")

PREFIX = "pi-player-"
HOTSPOT = PREFIX + "hotspot"
HOTSPOT_IP = "10.42.0.1"  # also in install.sh (dnsmasq wildcard) — keep in sync
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i: read off a screen, typed on a phone
WIFI_TYPES = ("wifi", "802-11-wireless")
NET_CHECK_EVERY = 5.0


# --- pure helpers ---------------------------------------------------------------------------

def split_t(line):
    """Split one line of `nmcli -t` output (':' separated, '\\:' and '\\\\' escaped)."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_devices(text):
    """`nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device` -> [(device, type, state, connection)]."""
    return [tuple(f) for f in (split_t(l) for l in text.splitlines() if l) if len(f) == 4]


def is_online(devices):
    """Any ethernet or wifi device fully connected, not counting our own hotspot.
    (NM's 'connected' = activated, i.e. IP config done.)"""
    return any(t in ("ethernet",) + WIFI_TYPES and s == "connected" and c != HOTSPOT
               for _, t, s, c in devices)


def parse_scan(text):
    """`nmcli -t -f SSID,SIGNAL,SECURITY device wifi list` -> [{ssid, signal, security}],
    one entry per SSID (strongest), hidden networks dropped, strongest first."""
    best = {}
    for f in (split_t(l) for l in text.splitlines() if l):
        if len(f) != 3 or not f[0]:
            continue
        try:
            sig = int(f[1])
        except ValueError:
            sig = 0
        if f[0] not in best or sig > best[f[0]]["signal"]:
            best[f[0]] = {"ssid": f[0], "signal": sig, "security": f[2]}
    return sorted(best.values(), key=lambda n: (-n["signal"], n["ssid"].lower()))


def parse_connections(text):
    """`nmcli -t -f NAME,TYPE connection show` -> [(name, type)]."""
    return [tuple(f) for f in (split_t(l) for l in text.splitlines() if l) if len(f) == 2]


def key_mgmt(security, password):
    """NM wifi-sec.key-mgmt for a scanned SECURITY string; None = open network."""
    if not password:
        return None
    if "WPA3" in security and "WPA2" not in security and "WPA1" not in security:
        return "sae"
    return "wpa-psk"


def wifi_qr(ssid, password):
    """Standard wifi-join QR payload (what phone cameras understand)."""
    def esc(s):
        return "".join("\\" + c if c in '\\;,:"' else c for c in s)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def hotspot_ssid(dev_id):
    return "Narrowcast-" + dev_id[-4:].upper()


def random_password(n=8):
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(n))


def friendly_error(stderr):
    s = stderr.lower()
    if "secrets were required" in s or "802-1x" in s or "psk" in s:
        return "Wrong password?"
    if "no network with ssid" in s or "not found" in s or "timeout" in s or "timed out" in s:
        return "Network not reachable."
    return "Could not connect."


# --- nmcli ----------------------------------------------------------------------------------

def available():
    return shutil.which("nmcli") is not None


def nm(*args, timeout=30):
    """Run nmcli; returns CompletedProcess (never raises on a non-zero exit)."""
    try:
        return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "timed out")


def devices():
    return parse_devices(nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", timeout=10).stdout)


def wifi_iface():
    for d, t, _, _ in devices():
        if t in WIFI_TYPES:
            return d
    return None


def route_ok(cms_url):
    """Fallback for dev machines without nmcli: is there a route to the CMS host (UDP connect
    sends nothing). DNS failure counts as offline."""
    u = urllib.parse.urlsplit(cms_url)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            s.connect((u.hostname or "8.8.8.8", u.port or 80))
            return True
    except OSError:
        return False


def online(cms_url):
    return is_online(devices()) if available() else route_ok(cms_url)


def scan(rescan=True):
    r = nm("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan",
           "yes" if rescan else "no", timeout=40)
    return parse_scan(r.stdout)


def start_hotspot(iface, ssid, password):
    nm("connection", "delete", HOTSPOT, timeout=15)
    nm("radio", "wifi", "on", timeout=10)
    r = nm("connection", "add", "type", "wifi", "ifname", iface, "con-name", HOTSPOT,
           "autoconnect", "no", "ssid", ssid, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
           "ipv4.method", "shared", "ipv4.addresses", HOTSPOT_IP + "/24", "ipv6.method", "disabled",
           "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password, "wifi-sec.proto", "rsn",
           "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp", "wifi-sec.pmf", "disable", timeout=15)
    if r.returncode == 0:
        r = nm("--wait", "30", "connection", "up", HOTSPOT, timeout=40)
    return r.returncode == 0, r.stderr.strip()


def stop_hotspot():
    nm("connection", "down", HOTSPOT, timeout=20)


def connect(iface, ssid, password, security="", hidden=False):
    """Create pi-player-<ssid> and bring it up. Returns (ok, error message)."""
    name = PREFIX + ssid
    nm("connection", "delete", name, timeout=15)
    args = ["connection", "add", "type", "wifi", "ifname", iface, "con-name", name, "ssid", ssid,
            "connection.autoconnect", "yes"]
    km = key_mgmt(security, password)
    if km:
        args += ["wifi-sec.key-mgmt", km, "wifi-sec.psk", password]
    if hidden:
        args += ["802-11-wireless.hidden", "yes"]
    r = nm(*args, timeout=15)
    if r.returncode == 0:
        r = nm("--wait", "30", "connection", "up", name, timeout=45)
    if r.returncode == 0:
        return True, None
    log.warning("wifi connect %r failed: %s", ssid, r.stderr.strip())
    nm("connection", "delete", name, timeout=15)
    return False, friendly_error(r.stderr)


def saved_wifi():
    """Saved wifi connections (ours and e.g. Pi Imager's 'preconfigured'), hotspot excluded."""
    out = nm("-t", "-f", "NAME,TYPE", "connection", "show", timeout=10).stdout
    return [n for n, t in parse_connections(out) if t in WIFI_TYPES and n != HOTSPOT]


def try_known(networks):
    """Bring up a saved wifi connection whose SSID is in range. Returns True when online."""
    in_range = {n["ssid"] for n in networks}
    for name in saved_wifi():
        ssid = nm("-g", "802-11-wireless.ssid", "connection", "show", name, timeout=10).stdout.strip()
        if ssid and ssid in in_range:
            log.info("wifi: trying saved network %r", ssid)
            if nm("--wait", "25", "connection", "up", name, timeout=35).returncode == 0:
                return True
    return False


def forget_ours():
    """Delete every NM connection we created (saved wifi + hotspot). Returns their names."""
    if not available():
        return []
    out = nm("-t", "-f", "NAME,TYPE", "connection", "show", timeout=10).stdout
    names = [n for n, _ in parse_connections(out) if n.startswith(PREFIX)]
    for n in names:
        nm("connection", "delete", n, timeout=15)
    return names


# --- background threads ---------------------------------------------------------------------

class Monitor:
    """Probes the network every NET_CHECK_EVERY s in a thread, so a slow nmcli/DNS never stalls
    the main loop. `ok` is the latest answer."""

    def __init__(self, cms_url):
        self.cms_url = cms_url
        self.ok = online(cms_url)
        self.wake = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="netmon", daemon=True).start()
        return self

    def poke(self):
        self.wake.set()

    def _loop(self):
        while True:
            self.wake.wait(NET_CHECK_EVERY)
            self.wake.clear()
            try:
                ok = online(self.cms_url)
            except Exception as e:
                log.warning("network probe failed: %s", e)
                ok = False
            if ok != self.ok:
                log.info("network %s", "back" if ok else "lost")
            self.ok = ok


class WifiSetup:
    """Runs the hotspot + captive portal while the main loop says we are in WIFI_SETUP.

    Main loop: want(bool) every tick, info() for the screen. Portal: networks, connect(), rescan().
    All nmcli work happens on this object's own thread."""

    HOLD_AFTER_CONNECT = 20.0  # AP stays down this long after a success, until the loop notices

    def __init__(self, dev_id, retry=120.0, monitor=None, port=80):
        self.ssid, self.password = hotspot_ssid(dev_id), random_password()
        self.retry, self.monitor, self.port = float(retry), monitor, port
        self.networks = []
        self.status = None     # transient line, e.g. "Connecting to X…"
        self.error = None      # last failure, shown on screen + page
        self.ap_up = False
        self.wanted = False
        self.activity = 0.0    # last portal request (postpones the retry while someone is busy)
        self.jobs = queue.Queue()
        self.portal = None
        self.iface = None
        self.scanned_at = -1e9

    def start(self):
        threading.Thread(target=self._loop, name="wifi", daemon=True).start()
        return self

    # called from other threads
    def want(self, on):
        if on != self.wanted:
            self.wanted = on
            self.jobs.put(None)

    def info(self):
        return (self.ssid if self.ap_up else None, self.password if self.ap_up else None,
                self.status, self.error)

    def touch(self):
        self.activity = time.monotonic()

    def connect(self, ssid, password):
        self.touch()
        self.jobs.put(("connect", ssid, password))

    def rescan(self):
        self.touch()
        self.jobs.put(("rescan",))

    # worker
    def _down(self):
        if self.portal:
            self.portal.stop()
            self.portal = None
        if self.ap_up:
            stop_hotspot()
            self.ap_up = False
            time.sleep(2)  # let the radio settle before scanning/connecting

    def _scan(self):
        nets = scan()
        if nets or not self.networks:
            self.networks = nets
        self.scanned_at = time.monotonic()
        log.info("wifi scan: %d networks", len(nets))

    def _up(self):
        from . import portal
        self.iface = self.iface or wifi_iface()
        if not self.iface:
            self.error = "No wifi adapter found — connect an ethernet cable."
            return False
        self.status = self.status or "Starting wifi setup…"
        if time.monotonic() - self.scanned_at > 10:  # retry path just scanned
            self._scan()
        ok, err = start_hotspot(self.iface, self.ssid, self.password)
        self.status = None
        if not ok:
            log.error("hotspot failed: %s", err)
            self.error = "Could not start the setup hotspot — connect an ethernet cable."
            return False
        self.ap_up = True
        log.info("hotspot %s up", self.ssid)
        for _ in range(10):  # the address can lag the activation a moment
            try:
                self.portal = portal.Portal(self, HOTSPOT_IP, self.port).start()
                break
            except OSError as e:
                log.warning("portal bind: %s", e)
                time.sleep(1)
        return True

    def _online(self):
        ok = is_online(devices())
        if ok and self.monitor:
            self.monitor.poke()
        return ok

    def _loop(self):
        last_try = hold = 0.0
        while True:
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                job = None
            try:
                now = time.monotonic()
                if not self.wanted:
                    self._down()
                    self.status = self.error = None
                    hold = 0.0
                    continue
                if job and job[0] == "connect":
                    _, ssid, pw = job
                    time.sleep(2)  # let the portal's answer reach the phone first
                    self.status, self.error = f"Connecting to {ssid}…", None
                    self._down()
                    sec = next((n["security"] for n in self.networks if n["ssid"] == ssid), None)
                    ok, err = connect(self.iface, ssid, pw, sec or "", hidden=sec is None)
                    self.status = None
                    if ok and self._online():
                        log.info("wifi: connected to %r", ssid)
                        hold = time.monotonic() + self.HOLD_AFTER_CONNECT
                        continue
                    self.error = f"Could not connect to {ssid}: {err or 'no connection'}"
                    last_try = time.monotonic()
                elif job and job[0] == "rescan":
                    time.sleep(2)
                    self.status = "Scanning for networks…"
                    self._down()
                    self.status = None
                elif self.ap_up and now - max(last_try, self.activity) >= self.retry:
                    last_try = now
                    self.status = "Checking saved networks…"
                    self._down()
                    self._scan()
                    found = try_known(self.networks) and self._online()
                    self.status = None
                    if found:
                        log.info("wifi: saved network back")
                        hold = time.monotonic() + self.HOLD_AFTER_CONNECT
                        continue
                if not self.ap_up and time.monotonic() >= hold:
                    if not self._up():
                        self.status = None
                        time.sleep(min(self.retry, 30))
            except Exception as e:  # never let the wifi thread die
                log.exception("wifi setup: %s", e)
                time.sleep(5)


def _selfcheck():
    assert split_t(r"My\:Net:80:WPA2") == ["My:Net", "80", "WPA2"]
    assert split_t(r"back\\slash:1:") == ["back\\slash", "1", ""]
    devs = parse_devices("wlan0:wifi:connected:pi-player-hotspot\neth0:ethernet:unavailable:\n"
                         "lo:loopback:connected (externally):lo\np2p-dev-wlan0:wifi-p2p:disconnected:\n")
    assert devs[0] == ("wlan0", "wifi", "connected", "pi-player-hotspot")
    assert not is_online(devs)  # our own hotspot + loopback are not "online"
    assert is_online(parse_devices("eth0:ethernet:connected:Wired connection 1\nwlan0:wifi:disconnected:\n"))
    assert is_online(parse_devices("wlan0:wifi:connected:pi-player-Kantine\n"))
    assert not is_online(parse_devices("wlan0:wifi:connecting (getting IP configuration):x\n"))
    nets = parse_scan("Kantine:40:WPA2\nKantine:72:WPA2\n:90:WPA2\nGuest\\:Free:55:\n"
                      "Office 5G:72:WPA2 WPA3\nNew:30:WPA3\nbad:x:WPA2\n")
    assert [n["ssid"] for n in nets] == ["Kantine", "Office 5G", "Guest:Free", "New", "bad"], nets
    assert nets[0]["signal"] == 72 and nets[2]["security"] == "" and nets[-1]["signal"] == 0
    assert parse_connections("preconfigured:802-11-wireless\nWired connection 1:802-3-ethernet\n"
                             "pi-player-hotspot:802-11-wireless\n") == [
        ("preconfigured", "802-11-wireless"), ("Wired connection 1", "802-3-ethernet"),
        ("pi-player-hotspot", "802-11-wireless")]
    assert key_mgmt("WPA2", "pw") == "wpa-psk" and key_mgmt("WPA2 WPA3", "pw") == "wpa-psk"
    assert key_mgmt("WPA3", "pw") == "sae" and key_mgmt("", "") is None
    assert wifi_qr("Narrowcast-AB12", "k7q4m2xa") == "WIFI:T:WPA;S:Narrowcast-AB12;P:k7q4m2xa;;"
    assert wifi_qr('a;b:c', 'p"\\') == 'WIFI:T:WPA;S:a\\;b\\:c;P:p\\"\\\\;;'
    assert hotspot_ssid("pi-00000000abcd1e2f") == "Narrowcast-1E2F"
    pw = random_password()
    assert len(pw) == 8 and set(pw) <= set(PW_ALPHABET)
    assert friendly_error("Error: Connection activation failed: Secrets were required, but not "
                          "provided.") == "Wrong password?"
    assert friendly_error("Error: No network with SSID 'x' found.") == "Network not reachable."
    assert route_ok("http://127.0.0.1:8080") is True
    print("netman self-check OK")


if __name__ == "__main__":
    _selfcheck()
