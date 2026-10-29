"""Wifi setup over Bluetooth LE for the App Clip: a BlueZ GATT server next to the hotspot portal.

Protocol: docs/ble-setup.md. Like portal.py this is only a front end: networks, status and errors
come from netman.WifiSetup, and connect/rescan go to its job queue, so all nmcli work stays on
the wifi thread. The D-Bus side runs a GLib main loop in its own thread. The same GATT application
carries the management service of a paired screen (blemanage.py, docs/ble-management.md); outside
the setup window this service is locked.

Self-check: python3 -m pi_player.blesetup
"""
import base64
import json
import logging
import os
import threading
import time
import urllib.parse

log = logging.getLogger("pi-player")

SERVICE_UUID = "8edd2610-ddcd-4435-a57e-98a6b4dd342f"
STATUS_UUID = "8edd2611-ddcd-4435-a57e-98a6b4dd342f"
NETWORKS_UUID = "8edd2612-ddcd-4435-a57e-98a6b4dd342f"
COMMAND_UUID = "8edd2613-ddcd-4435-a57e-98a6b4dd342f"
VERSION = 1
AAD = b"zz-wifi-setup-v1"
SALT = b"zz-narrowcasting"
MAX_VALUE = 512
LINGER = 120.0  # keep advertising this long after WIFI_SETUP, so the phone sees the result
RETRY = 10.0
PENDING_MAX = 150.0  # a connect is at most ~2 min of nmcli + CMS checks


class Unauthorized(Exception):
    pass


class BadCommand(Exception):
    pass


# --- pure protocol --------------------------------------------------------------------------

def new_token():
    return os.urandom(16)


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def short_id(dev_id):
    return dev_id[-4:].upper()


def local_name(dev_id):
    return "ZZ-" + short_id(dev_id)


def setup_url(base, dev_id, token):
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urllib.parse.urlencode({'d': short_id(dev_id), 'k': b64url(token)})}"


def derive_key(token):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=AAD).derive(token)


def seal(token, command, nonce=None):
    """Encrypt a command into a COMMAND frame (what the app does; used by the self-check)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = nonce or os.urandom(12)
    body = nonce + AESGCM(derive_key(token)).encrypt(nonce, json.dumps(command).encode(), AAD)
    return len(body).to_bytes(2, "big") + body


def open_frame(token, frame):
    """COMMAND frame -> command dict. Unauthorized: wrong token or tampered. BadCommand: garbage."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(frame) < 2 + 12 + 16 or int.from_bytes(frame[:2], "big") != len(frame) - 2:
        raise BadCommand("bad frame length")
    try:
        plain = AESGCM(derive_key(token)).decrypt(frame[2:14], frame[14:], AAD)
    except InvalidTag:
        raise Unauthorized("frame does not decrypt with this screen's token")
    try:
        cmd = json.loads(plain)
    except ValueError:
        raise BadCommand("not JSON")
    if not isinstance(cmd, dict) or cmd.get("op") not in ("connect", "scan"):
        raise BadCommand("unknown op")
    return cmd


class Reassembler:
    """Collects a COMMAND frame from WriteValue calls (a long write arrives in parts with offsets).
    write() returns the whole frame once its length prefix is satisfied, else None."""

    def __init__(self):
        self.buf = bytearray()

    def write(self, offset, value):
        if offset == 0:
            self.buf = bytearray()
        if offset != len(self.buf) or offset + len(value) > MAX_VALUE:
            self.buf = bytearray()
            raise BadCommand(f"unexpected write offset {offset}")
        self.buf += value
        if len(self.buf) >= 2 and len(self.buf) - 2 >= int.from_bytes(self.buf[:2], "big"):
            frame, self.buf = bytes(self.buf), bytearray()
            return frame
        return None


def encode_networks(networks, limit=MAX_VALUE):
    """netman scan list (strongest first) -> NETWORKS value, weakest dropped to fit `limit`."""
    rows = [[n["ssid"], int(n["signal"]), 1 if n["security"] else 0] for n in networks]
    while True:
        data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) <= limit:
            return data
        rows.pop()


def connect_args(cmd):
    """A connect command -> (ssid, password, error). Same rules as the portal form."""
    from .portal import validate
    ssid = cmd.get("ssid") if isinstance(cmd.get("ssid"), str) else ""
    pw = cmd.get("password") if isinstance(cmd.get("password"), str) else ""
    form = {"other" if cmd.get("hidden") else "ssid": [ssid], "password": [pw]}
    return validate(form)


def status(wifi, wanted, joined, rejected, pair_info, paired, pending=False, seen_error=None):
    """STATUS fields (without seq) from the controller's state. Pure; see docs/ble-setup.md.

    busy only while a command from the phone is in flight (pending): the hotspot restarting after
    a failure or the saved-network retry must not replace the failure the phone is showing.
    seen_error: an error the phone already had before it asked for a rescan, not shown again."""
    st = {"v": VERSION, "state": "ready", "msg": None, "ssid": None, "scan": wifi.get("scan", 0),
          "code": None, "url": None}
    if not wanted or joined:
        if paired:
            st.update(state="paired", ssid=joined)
            return st
        st.update(state="online", ssid=joined)
        if pair_info and pair_info.get("code"):
            st.update(code=pair_info["code"], url=pair_info.get("pair_url"))
        return st
    if rejected:
        st.update(state="failed", msg=rejected)
    elif pending:
        st.update(state="busy", msg=wifi.get("status") or "Bezig…")
    elif wifi.get("error") and wifi["error"] != seen_error:
        st.update(state="failed", msg=wifi["error"])
    return st


def encode_status(st, seq):
    data = json.dumps(dict(st, seq=seq), ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > MAX_VALUE:
        data = json.dumps(dict(st, seq=seq, msg=(st["msg"] or "")[:200], url=None),
                          ensure_ascii=False, separators=(",", ":")).encode()
    return data


# --- BlueZ (D-Bus) --------------------------------------------------------------------------

BLUEZ = "org.bluez"
APP_PATH = "/nl/zekerzichtbaar/pi_player"
ADV_PATH = APP_PATH + "/advertisement0"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHRC = "org.bluez.GattCharacteristic1"
LE_ADV = "org.bluez.LEAdvertisement1"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS = "org.freedesktop.DBus.Properties"


def available():
    try:
        import dbus  # noqa: F401
        import cryptography  # noqa: F401
        from gi.repository import GLib  # noqa: F401
    except ImportError as e:
        log.warning("bluetooth setup disabled: %s", e)
        return False
    return True


class BleSetup:
    """GATT server + advertisement for the WifiSetup controller `wifi`.

    pairing: callable -> the Pairing.info dict or None; paired: callable -> bool."""

    def __init__(self, wifi, dev_id, base_url, pairing=lambda: None, paired=lambda: False, manage=None):
        self.wifi, self.dev_id = wifi, dev_id
        self.pairing, self.paired = pairing, paired
        self.manage = manage
        if manage:
            manage.busy = lambda: bool(self.pending)
        self.token = wifi.token
        self.url = setup_url(base_url, dev_id, self.token)
        self.name = local_name(dev_id)
        self.seq = 0
        self.status_value = b""
        self.last_status = None
        self.was_wanted = False
        self.networks_value = b"[]"
        self.networks_src = None
        self.rejected = None
        self.pending = None      # ("connect", attempts) / ("scan", scan_id) at the time of the command
        self.pending_since = 0.0
        self.seen_error = None
        self.wanted_until = 0.0
        self.setup_open = False
        self.advertising = None  # None / "setup" / "manage": the advertisement registered now
        self.adv_kind = None
        self.adv_busy = False
        self.adv_next = 0.0
        self.registered = False
        self.bluez_up = False
        self.next_try = 0.0
        self.adapter = None
        self.bus = None
        self.status_chrc = None
        self.reassembler = Reassembler()

    def start(self):
        threading.Thread(target=self._run, name="ble", daemon=True).start()
        return self

    # called on the GLib thread
    def setup_read(self, value, offset=0):
        if not self.setup_open:
            raise Unauthorized("setup service locked outside the setup window")
        self.wifi.touch()
        return value[offset:]

    def setup_write(self, offset, value):
        if not self.setup_open:
            raise Unauthorized("setup service locked outside the setup window")
        frame = self.reassembler.write(offset, value)
        if frame is not None:
            self.handle_frame(frame)

    def handle_frame(self, frame):
        cmd = open_frame(self.token, frame)
        w = self.wifi
        w.touch()
        self.rejected = None
        if cmd["op"] == "scan":
            self.seen_error = w.error
            self._pend("scan", w.scan_id)
            w.rescan()
        else:
            ssid, pw, err = connect_args(cmd)
            if err:
                self.rejected, self.pending = err, None
            else:
                log.info("bluetooth: connect to %r requested", ssid)
                self.seen_error = None
                self._pend("connect", w.attempts)
                w.connect(ssid, pw, hidden=bool(cmd.get("hidden")))
        self.refresh()

    def _pend(self, op, mark):
        self.pending, self.pending_since = (op, mark), time.monotonic()

    def _settle(self, now):
        """Drop the pending command once the controller finished it (or it is clearly lost)."""
        if not self.pending:
            return
        op, mark = self.pending
        w = self.wifi
        done = w.attempts != mark if op == "connect" else w.scan_id != mark
        if done or not w.wanted or now - self.pending_since > PENDING_MAX:
            self.pending = None

    def refresh(self):
        """Recompute STATUS/NETWORKS from the controller; notify on change. Returns True (GLib)."""
        try:
            self._refresh()
        except Exception as e:
            log.exception("bluetooth refresh: %s", e)
        return True

    def _refresh(self):
        w = self.wifi
        now = time.monotonic()
        if w.wanted and not self.was_wanted:
            self.rejected = self.seen_error = None
        self._settle(now)
        self.was_wanted = w.wanted
        if w.wanted:
            self.wanted_until = now + LINGER
        if w.networks is not self.networks_src:
            self.networks_src = w.networks
            self.networks_value = encode_networks(w.networks)
        st = status({"status": w.status, "error": w.error, "scan": w.scan_id}, w.wanted, w.joined,
                    self.rejected, self.pairing(), self.paired(), bool(self.pending), self.seen_error)
        if st != self.last_status:
            self.last_status = st
            self.seq += 1
            self.status_value = encode_status(st, self.seq)
            if self.status_chrc:
                self.status_chrc.changed(self.status_value)
        if self.bluez_up and not self.registered and now >= self.next_try:
            self.next_try = now + RETRY
            self._register()
        from .blemanage import advert_kind
        self.setup_open = w.wanted or now < self.wanted_until
        if self.manage:
            self.manage.tick()
        self._advertise(advert_kind(self.setup_open, bool(self.manage and not self.manage.locked)))

    def _advertise(self, kind):
        """Async on purpose: bluetoothd reads the advertisement's properties back from us while the
        call is pending, and we are the GLib thread that has to answer. It reads them at register
        time, so another service UUID means unregister first, then register on a later refresh."""
        now = time.monotonic()
        if not self.registered or kind == self.advertising or self.adv_busy or now < self.adv_next:
            return
        on = self.advertising is None
        self.adv_busy, self.adv_next = True, now + RETRY
        if on:
            self.adv_kind = kind

        def ok():
            self.adv_busy, self.adv_next = False, 0.0
            self.advertising = kind if on else None
            log.info("bluetooth: %s", f"advertising as {self.name} ({kind})" if on else "advertising stopped")

        def fail(e):
            self.adv_busy = False
            if not on:
                self.advertising = None
            log.warning("bluetooth advertising %s: %s", "start" if on else "stop", e)

        mgr = self._iface("org.bluez.LEAdvertisingManager1")
        call = mgr.RegisterAdvertisement if on else mgr.UnregisterAdvertisement
        args = (ADV_PATH, {}) if on else (ADV_PATH,)
        call(*args, reply_handler=ok, error_handler=fail)

    def _iface(self, name):
        import dbus
        return dbus.Interface(self.bus.get_object(BLUEZ, self.adapter), name)

    def _run(self):
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        objs = _objects(self, dbus)
        self.status_chrc = objs["status"]
        if self.manage:
            self.manage.notify = objs["response"].changed
        self.bus.watch_name_owner(BLUEZ, self._bluez_owner)
        self.bus.add_signal_receiver(self._device_changed, signal_name="PropertiesChanged",
                                     dbus_interface=DBUS_PROPS, bus_name=BLUEZ, arg0="org.bluez.Device1",
                                     path_keyword="path")
        self.bus.add_signal_receiver(self._device_removed, signal_name="InterfacesRemoved",
                                     dbus_interface=DBUS_OM, bus_name=BLUEZ)
        GLib.timeout_add(500, self.refresh)
        GLib.MainLoop().run()

    def _device_changed(self, interface, changed, invalidated, path=None):
        if self.manage and "Connected" in changed and not changed["Connected"]:
            self.manage.end(str(path))

    def _device_removed(self, path, interfaces):
        if self.manage and "org.bluez.Device1" in interfaces:
            self.manage.end(str(path))

    def _bluez_owner(self, owner):
        """bluetoothd (re)started or went away: register again on the next refresh."""
        self.registered = self.adv_busy = False
        self.advertising = None
        if self.manage:
            for dev in list(self.manage.sessions):
                self.manage.end(dev)
        self.adv_next = 0.0
        self.bluez_up, self.next_try = bool(owner), 0.0
        if not owner:
            log.warning("bluetooth: bluetoothd is gone")

    def _register(self):
        """Find the adapter, power it, register the GATT app. Failures retry every RETRY s (the
        Pi's UART adapter can show up after bluetoothd, rfkill can block it for a while)."""
        try:
            self._register_app()
        except Exception as e:
            log.warning("bluetooth: registering failed: %s", e)

    def _register_app(self):
        import dbus
        om = dbus.Interface(self.bus.get_object(BLUEZ, "/"), DBUS_OM)
        self.adapter = next((p for p, ifs in om.GetManagedObjects().items()
                             if "org.bluez.GattManager1" in ifs and "org.bluez.LEAdvertisingManager1" in ifs),
                            None)
        if not self.adapter:
            log.warning("bluetooth: no adapter with GATT + LE advertising")
            return
        props = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter), DBUS_PROPS)
        props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(False))
        props.Set("org.bluez.Adapter1", "Alias", dbus.String(self.name))  # GAP name, not the hostname

        def ok():
            self.registered = True
            log.info("bluetooth: wifi setup + management services registered on %s", self.adapter)
            self.refresh()

        def fail(e):
            log.warning("bluetooth: RegisterApplication failed: %s", e)

        self._iface("org.bluez.GattManager1").RegisterApplication(
            APP_PATH, {}, reply_handler=ok, error_handler=fail)


def _objects(ble, dbus):
    """Export the GATT application (setup service + management service, 3 characteristics each)
    and the advertisement."""
    import dbus.service
    from . import blemanage

    class Unauth(dbus.exceptions.DBusException):
        _dbus_error_name = "org.bluez.Error.NotAuthorized"

    class Failed(dbus.exceptions.DBusException):
        _dbus_error_name = "org.bluez.Error.Failed"

    class Unlikely(dbus.exceptions.DBusException):
        _dbus_error_name = "org.bluez.Error.InvalidArguments"  # not mapped by bluetoothd: ATT 0x0e

    class NotSupported(dbus.exceptions.DBusException):
        _dbus_error_name = "org.bluez.Error.NotSupported"

    def byte_array(data):
        return dbus.Array([dbus.Byte(b) for b in data], signature="y")

    def offset_of(options):
        return int(options.get("offset", 0))

    def device_of(options):
        return str(options.get("device", ""))

    class Chrc(dbus.service.Object):
        def __init__(self, service, path, uuid, flags):
            self.service, self.path, self.uuid, self.flags = service, path, uuid, flags
            self.notifying = False
            dbus.service.Object.__init__(self, ble.bus, path)

        def props(self):
            return {GATT_CHRC: {"Service": dbus.ObjectPath(self.service), "UUID": self.uuid,
                                "Flags": dbus.Array(self.flags, signature="s")}}

        def value(self):
            return b""

        def read(self, options):
            try:
                return ble.setup_read(self.value(), offset_of(options))
            except Unauthorized as e:
                raise Unauth(str(e))

        @dbus.service.method(DBUS_PROPS, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.props()[interface][prop]

        @dbus.service.method(DBUS_PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.props()[interface]

        @dbus.service.signal(DBUS_PROPS, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        @dbus.service.method(GATT_CHRC, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            return byte_array(self.read(options))

        @dbus.service.method(GATT_CHRC, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            raise NotSupported("read only")

        @dbus.service.method(GATT_CHRC)
        def StartNotify(self):
            self.notifying = True

        @dbus.service.method(GATT_CHRC)
        def StopNotify(self):
            self.notifying = False

        def changed(self, data):
            if self.notifying:
                self.PropertiesChanged(GATT_CHRC, {"Value": byte_array(data)}, [])

    class Status(Chrc):
        def value(self):
            return ble.status_value

        def changed(self, data):
            if ble.setup_open:
                Chrc.changed(self, data)

    class Networks(Chrc):
        def value(self):
            return ble.networks_value

    class Command(Chrc):
        @dbus.service.method(GATT_CHRC, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            try:
                ble.setup_write(offset_of(options), bytes(value))
            except Unauthorized as e:
                log.warning("bluetooth: rejected command: %s", e)
                raise Unauth(str(e))
            except BadCommand as e:
                log.warning("bluetooth: bad command: %s", e)
                raise Failed(str(e))

    def managed(call):
        if not ble.manage:
            raise Unauth("management not available")
        try:
            return call()
        except blemanage.Unauthorized as e:
            log.warning("bluetooth management: rejected: %s", e)
            raise Unauth(str(e))
        except blemanage.BadFrame as e:
            log.warning("bluetooth management: bad write: %s", e)
            raise Unlikely(str(e))

    class Session(Chrc):
        def read(self, options):
            return managed(lambda: ble.manage.session_read(device_of(options), offset_of(options)))

        @dbus.service.method(GATT_CHRC, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            managed(lambda: ble.manage.session_write(device_of(options), offset_of(options), bytes(value)))

    class Request(Chrc):
        @dbus.service.method(GATT_CHRC, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            managed(lambda: ble.manage.request_write(device_of(options), offset_of(options), bytes(value)))

    class Response(Chrc):
        def read(self, options):
            return managed(lambda: ble.manage.response_read(device_of(options), offset_of(options)))

    setup, manage = APP_PATH + "/service0", APP_PATH + "/service1"
    services = {
        setup: (SERVICE_UUID, [Status(setup, setup + "/char0", STATUS_UUID, ["read", "notify"]),
                               Networks(setup, setup + "/char1", NETWORKS_UUID, ["read"]),
                               Command(setup, setup + "/char2", COMMAND_UUID, ["write"])]),
        manage: (blemanage.SERVICE_UUID,
                 [Session(manage, manage + "/char0", blemanage.SESSION_UUID, ["read", "write"]),
                  Request(manage, manage + "/char1", blemanage.REQUEST_UUID, ["write"]),
                  Response(manage, manage + "/char2", blemanage.RESPONSE_UUID, ["read", "notify"])]),
    }

    class Service(dbus.service.Object):
        def __init__(self, path, uuid, chrcs):
            self.path, self.uuid, self.chrcs = path, uuid, chrcs
            dbus.service.Object.__init__(self, ble.bus, path)

        def props(self):
            return {GATT_SERVICE: {"UUID": self.uuid, "Primary": True,
                                   "Characteristics": dbus.Array([dbus.ObjectPath(c.path) for c in self.chrcs],
                                                                 signature="o")}}

        @dbus.service.method(DBUS_PROPS, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.props()[interface][prop]

        @dbus.service.method(DBUS_PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.props()[interface]

    svcs = [Service(path, uuid, chrcs) for path, (uuid, chrcs) in services.items()]

    class App(dbus.service.Object):
        @dbus.service.method(DBUS_OM, out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            out = {}
            for svc in svcs:
                out[dbus.ObjectPath(svc.path)] = svc.props()
                for c in svc.chrcs:
                    out[dbus.ObjectPath(c.path)] = c.props()
            return out

    class Advertisement(dbus.service.Object):
        def props(self):
            uuid = SERVICE_UUID if ble.adv_kind == "setup" else blemanage.SERVICE_UUID
            return {LE_ADV: {"Type": "peripheral",
                             "ServiceUUIDs": dbus.Array([uuid], signature="s"),
                             "LocalName": dbus.String(ble.name),
                             "Discoverable": dbus.Boolean(True)}}

        @dbus.service.method(DBUS_PROPS, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            return self.props()[interface][prop]

        @dbus.service.method(DBUS_PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.props()[interface]

        @dbus.service.method(LE_ADV)
        def Release(self):
            ble.advertising = None

    App(ble.bus, APP_PATH)
    Advertisement(ble.bus, ADV_PATH)
    return {"status": services[setup][1][0], "response": services[manage][1][2]}


VECTOR_TOKEN = bytes(range(16))
VECTOR_NONCE = bytes(range(100, 112))
VECTOR_COMMAND = {"op": "connect", "ssid": "Kantine", "password": "geheim123", "hidden": False}


def _selfcheck():
    tok = VECTOR_TOKEN
    assert b64url(tok) == "AAECAwQFBgcICQoLDA0ODw" and len(b64url(new_token())) == 22
    assert local_name("pi-00000000abcd1e2f") == "ZZ-1E2F"
    assert setup_url("https://app.zekerzichtbaar.tv/setup", "pi-00000000abcd1e2f", tok) == \
        "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw"
    assert setup_url("https://x/s?a=1", "pi-1e2f", tok).startswith("https://x/s?a=1&d=1E2F&")

    frame = seal(tok, VECTOR_COMMAND, VECTOR_NONCE)
    assert open_frame(tok, frame) == VECTOR_COMMAND
    assert open_frame(tok, seal(tok, {"op": "scan"})) == {"op": "scan"}
    for bad, exc in ((seal(new_token(), {"op": "scan"}), Unauthorized),
                     (frame[:-1] + bytes([frame[-1] ^ 1]), Unauthorized),
                     (frame[:-1], BadCommand), (b"\x00\x01x", BadCommand),
                     (seal(tok, {"op": "reboot"}), BadCommand), (seal(tok, [1]), BadCommand)):
        try:
            open_frame(tok, bad)
            raise AssertionError(f"accepted {bad!r}")
        except exc:
            pass

    r = Reassembler()
    assert r.write(0, frame[:100]) is None and r.write(100, frame[100:]) == frame
    assert r.write(0, frame) == frame  # in one go
    r.write(0, frame[:10])
    try:
        r.write(50, frame[50:])
        raise AssertionError("gap accepted")
    except BadCommand:
        pass
    assert r.write(0, frame) == frame  # a fresh write after a broken one works

    nets = [{"ssid": "Kantine", "signal": 82, "security": "WPA2"}, {"ssid": "Gast", "signal": 20, "security": ""}]
    assert json.loads(encode_networks(nets)) == [["Kantine", 82, 1], ["Gast", 20, 0]]
    many = [{"ssid": f"Netwerk met een lange naam {i:02d}", "signal": 90 - i, "security": "WPA2"} for i in range(40)]
    big = encode_networks(many)
    assert len(big) <= MAX_VALUE and json.loads(big)[0][0].endswith("00"), len(big)
    assert encode_networks([]) == b"[]"

    assert connect_args({"op": "connect", "ssid": "Kantine", "password": "geheim123"}) == ("Kantine", "geheim123", None)
    assert connect_args({"op": "connect", "ssid": "Verstopt", "password": "", "hidden": True}) == ("Verstopt", "", None)
    assert connect_args({"op": "connect", "ssid": "K", "password": "kort"})[2]
    assert connect_args({"op": "connect", "ssid": 5})[2]

    w = {"status": None, "error": None, "scan": 2}
    assert status(w, True, None, None, None, False)["state"] == "ready"
    assert status(dict(w, status="Verbinden met X…"), True, None, None, None, False, True)["msg"] == "Verbinden met X…"
    assert status(w, True, None, None, None, False, True) == dict(status(w, True, None, None, None, False),
                                                                  state="busy", msg="Bezig…")
    assert status(dict(w, error="mislukt"), True, None, None, None, False)["state"] == "failed"
    # after a failure the hotspot comes back ("Wifi-instelling starten…"): still failed, not busy
    assert status(dict(w, error="mislukt", status="Wifi-instelling starten…"), True, None, None, None,
                  False)["state"] == "failed"
    assert status(dict(w, error="mislukt"), True, None, None, None, False, seen_error="mislukt")["state"] == "ready"
    assert status(w, True, None, "Een wifi-wachtwoord is 8 tot 63 tekens.", None, False)["state"] == "failed"
    info = {"code": "K7Q-4M2", "pair_url": "https://cms/schermen?code=K7Q-4M2"}
    on = status(w, True, "Kantine", None, info, False)
    assert on["state"] == "online" and on["ssid"] == "Kantine" and on["code"] == "K7Q-4M2", on
    assert status(w, False, None, None, None, True)["state"] == "paired"
    data = encode_status(status(dict(w, error="x" * 600), True, None, None, None, False), 9)
    assert len(data) <= MAX_VALUE and json.loads(data)["seq"] == 9
    _selfcheck_service()
    from . import blemanage
    blemanage._selfcheck()
    print("blesetup self-check OK")


def _selfcheck_service():
    """Setup service locked outside the setup window, advertising per docs/ble-management.md."""
    from . import blemanage, netman
    dev = "pi-00000000abcd1e2f"
    w = netman.WifiSetup(dev)
    store = {"s": None}
    m = blemanage.Manager(lambda: store["s"], w)
    ble = BleSetup(w, dev, "https://app.zekerzichtbaar.tv/setup", manage=m)
    adv = []

    class AdvManager:
        def RegisterAdvertisement(self, path, opts, reply_handler, error_handler):
            adv.append(("register", ble.adv_kind))
            reply_handler()

        def UnregisterAdvertisement(self, path, reply_handler, error_handler):
            adv.append(("unregister",))
            reply_handler()

    ble._iface = lambda name: AdvManager()
    ble.registered = True

    def locked():
        for call in (lambda: ble.setup_read(ble.status_value), lambda: ble.setup_read(ble.networks_value, 2),
                     lambda: ble.setup_write(0, seal(w.token, {"op": "scan"}))):
            try:
                call()
                raise AssertionError("setup service readable outside the setup window")
            except Unauthorized:
                pass
        return w.jobs.empty()

    ble._refresh()  # unpaired, no secret, not in WIFI_SETUP: nothing advertised, all locked
    assert not ble.setup_open and ble.advertising is None and adv == [] and locked()
    assert m.locked
    w.wanted = True  # WIFI_SETUP: the setup service as before
    ble._refresh()
    assert ble.setup_open and ble.advertising == "setup" and adv == [("register", "setup")]
    assert ble.setup_read(ble.status_value) == ble.status_value and json.loads(ble.status_value)["state"] == "ready"
    assert ble.setup_read(ble.networks_value, 1) == ble.networks_value[1:]
    ble.setup_write(0, seal(w.token, {"op": "scan"}))
    assert w.jobs.get_nowait() == ("rescan",) and ble.pending and m.busy()
    ble.pending = None
    store["s"] = "S" * 43  # paired in the window: still the setup advertisement
    ble._refresh()
    assert not m.locked and adv == [("register", "setup")]
    w.wanted = False  # left WIFI_SETUP: setup for LINGER more, so the phone sees the result
    ble._refresh()
    assert ble.setup_open and ble.advertising == "setup"
    ble.wanted_until = 0.0  # LINGER over: locked, management advertised instead
    ble._refresh()
    assert not ble.setup_open and ble.advertising is None and adv[-1] == ("unregister",) and locked()
    ble._refresh()
    assert ble.advertising == "manage" and adv[-1] == ("register", "manage")
    ble._refresh()
    assert len(adv) == 3
    store["s"] = "R" * 43  # rotation: same advertisement, no re-registration
    ble._refresh()
    assert len(adv) == 3 and ble.advertising == "manage"
    store["s"] = None  # unpaired (401) or withdrawn: nothing advertised, management locked
    ble._refresh()
    assert ble.advertising is None and adv[-1] == ("unregister",) and m.locked
    ble._refresh()
    assert len(adv) == 4


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["vector"]:
        print(json.dumps({"token_b64url": b64url(VECTOR_TOKEN), "nonce_hex": VECTOR_NONCE.hex(),
                          "key_hex": derive_key(VECTOR_TOKEN).hex(), "command": VECTOR_COMMAND,
                          "frame_hex": seal(VECTOR_TOKEN, VECTOR_COMMAND, VECTOR_NONCE).hex()}, indent=1))
        sys.exit()
    _selfcheck()
