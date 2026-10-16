import os
import re

# Release tag (e.g. "v1.2.3") written to pi_player/VERSION by install.sh / image/build.sh.
# No file (a plain checkout) = "dev".
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def read_version(path=os.path.join(os.path.dirname(__file__), "VERSION")):
    try:
        with open(path) as f:
            v = f.read().strip()
    except OSError:
        return "dev"
    return v if VERSION_RE.match(v) else "dev"


__version__ = read_version()
