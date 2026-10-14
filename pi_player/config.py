"""Load /etc/pi-player/config.toml over built-in defaults."""
import os
import tomllib

DEFAULT_PATH = "/etc/pi-player/config.toml"

DEFAULTS = {
    "cms_url": "http://localhost:8080",
    "state_dir": "/var/lib/pi-player",
    "video": "",
    "epoch": 0,
    "manifest_poll": 30,
    "offline_timeout": 600,
    "wifi_retry": 120,
    "logo": "/etc/pi-player/logo.png",
    "ntp_server": "",
    "display_mode": "1920x1080@60",
    "video_sink": "kmssink",
    "sync": {"interval": 1.0, "seek_threshold": 0.5},
}


def load(path=None):
    path = path or os.environ.get("PI_PLAYER_CONFIG", DEFAULT_PATH)
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    if os.path.exists(path):
        with open(path, "rb") as f:
            for k, v in tomllib.load(f).items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
    if not cfg["video"]:
        cfg["video"] = os.path.join(cfg["state_dir"], "current.mp4")
    return cfg
