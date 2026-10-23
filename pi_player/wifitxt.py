"""wifi.txt on the boot partition: set the wifi without a phone (filled in on a laptop after flashing).

Format: `key=value` lines, UTF-8 (a BOM and CRLF from Notepad are fine), `#` at the start of a line
is a comment. Keys: `ssid`, `password` (empty = open network), optional `hidden=yes`. Everything
after the first `=` is the value (so an SSID may contain `=`, `:` or `#`); spaces around it are
stripped, as is one pair of surrounding double quotes.

The agent reads it once at start (netman.WifiSetup), then `retire()` writes `wifi.txt.done` with
`password=***` and removes `wifi.txt`, so no readable password stays on the FAT partition. A file
without any value (the template in the image) is left alone.

Self-check: python3 -m pi_player.wifitxt
"""
import os

NAME = "wifi.txt"
BOOT_DIRS = ("/boot/firmware", "/boot")  # Bookworm mounts the FAT partition at /boot/firmware
YES = ("yes", "ja", "true", "1", "y", "j")


def default_path():
    for d in BOOT_DIRS:
        if os.path.isdir(d):
            return os.path.join(d, NAME)
    return os.path.join(BOOT_DIRS[0], NAME)


def _lines(text):
    if text.startswith("﻿"):
        text = text[1:]
    return text.splitlines(keepends=True)


def _split(line):
    """(key, value) of a setting line, or None for blank lines and comments."""
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    key, value = s.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return key.strip().lower(), value


def parse(text):
    """-> {ssid, password, hidden}, or None when nothing is filled in. ValueError (Dutch) when the
    file is filled in but unusable."""
    found = {}
    for line in _lines(text):
        kv = _split(line)
        if kv and kv[0] in ("ssid", "password", "hidden"):
            found[kv[0]] = kv[1]
    if not found.get("ssid") and not found.get("password"):
        return None  # the empty template (hidden=no alone is not "filled in")
    ssid, pw = found.get("ssid", ""), found.get("password", "")
    if not ssid:
        raise ValueError("geen netwerknaam (ssid=) ingevuld")
    if len(ssid.encode()) > 32:
        raise ValueError("netwerknaam is langer dan 32 tekens")
    if pw and not (8 <= len(pw) <= 63 or (len(pw) == 64 and all(c in "0123456789abcdefABCDEF" for c in pw))):
        raise ValueError("wachtwoord moet 8 tot 63 tekens zijn")
    return {"ssid": ssid, "password": pw, "hidden": found.get("hidden", "").lower() in YES}


def masked(text):
    """The file with every password value replaced by `***` (comments and line endings kept)."""
    out = []
    for line in _lines(text):
        kv = _split(line)
        if kv and kv[0] == "password" and kv[1]:
            end = line[len(line.rstrip("\r\n")):]
            out.append("password=***" + end)
        else:
            out.append(line)
    return "".join(out)


def read(path):
    """-> (settings or None, error or None). Missing file = (None, None)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"niet te lezen ({e.strerror})"
    try:
        return parse(raw.decode("utf-8")), None
    except UnicodeDecodeError:
        # Notepad before 2019 / other editors: ANSI. Better a likely-right SSID than nothing.
        return _parse_or_error(raw.decode("cp1252", errors="replace"))
    except ValueError as e:
        return None, str(e)


def _parse_or_error(text):
    try:
        return parse(text), None
    except ValueError as e:
        return None, str(e)


def retire(path):
    """wifi.txt -> wifi.txt.done with the password masked. True when nothing readable is left."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
        done = path + ".done"
        with open(done, "w", encoding="utf-8", newline="") as f:
            f.write(masked(text))
            f.flush()
            os.fsync(f.fileno())
        os.remove(path)
        return True
    except OSError:
        return False


def _selfcheck():
    import tempfile
    notepad = ("﻿# Wifi voor het scherm\r\n"
               "  ssid  =  Kantine: BCS #2  \r\n"
               "password = geheim=123#!\r\n"
               "\r\n"
               "# hidden=yes\r\n")
    assert parse(notepad) == {"ssid": "Kantine: BCS #2", "password": "geheim=123#!", "hidden": False}
    assert parse("SSID=\"Guest Net\"\nPassword=\"a b c d e f\"\nhidden = Yes\n") == {
        "ssid": "Guest Net", "password": "a b c d e f", "hidden": True}
    assert parse("ssid=Open\n") == {"ssid": "Open", "password": "", "hidden": False}
    assert parse("") is None and parse("# ssid=\n# password=\nssid=\npassword=\nhidden=no\n") is None
    for bad, why in (("password=12345678\n", "netwerknaam"), ("ssid=x\npassword=kort\n", "8 tot 63"),
                     ("ssid=" + "x" * 33 + "\n", "32")):
        try:
            parse(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert why in str(e), e
    assert parse("ssid=x\npassword=" + "ab" * 32 + "\n")["password"] == "ab" * 32  # 64 hex = raw PSK

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, NAME)
        assert read(p) == (None, None)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(notepad)
        cfg, err = read(p)
        assert err is None and cfg["ssid"] == "Kantine: BCS #2" and cfg["password"] == "geheim=123#!"
        assert retire(p)
        assert os.listdir(d) == [NAME + ".done"]
        with open(p + ".done", encoding="utf-8", newline="") as f:
            done = f.read()
        assert "geheim" not in done and "password=***\r\n" in done and "ssid  =  Kantine: BCS #2" in done
        with open(p, "wb") as f:
            f.write("ssid=Caf\xe9\npassword=12345678\n".encode("cp1252"))
        assert read(p) == ({"ssid": "Café", "password": "12345678", "hidden": False}, None)
        with open(p, "w") as f:
            f.write("password=12345678\n")
        assert read(p) == (None, "geen netwerknaam (ssid=) ingevuld")
    print("wifitxt self-check OK")


if __name__ == "__main__":
    _selfcheck()
