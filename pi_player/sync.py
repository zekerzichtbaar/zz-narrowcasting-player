"""Clock-based sync math. Pure functions; self-check: python3 -m pi_player.sync"""

DEFAULTS = {
    "deadband": 0.020,     # |drift| below this (s): do nothing (~half a frame at 25 fps)
    "seek_threshold": 0.5, # |drift| above this (s): hard seek
    "gain": 0.5,           # speed = 1 - gain * drift   (drift in s); P-only, < 2 for stability at 1 s interval
    "max_nudge": 0.02,     # speed clamped to 1 +/- max_nudge
}


def target_position(now, epoch, duration):
    """Where every screen in the group should be right now (seconds into the video)."""
    return (now - epoch) % duration


def drift(actual, target, duration):
    """actual - target, wrapped to [-duration/2, duration/2) so a loop wrap isn't a huge drift."""
    d = (actual - target) % duration
    return d - duration if d >= duration / 2 else d


def correction(drift, thresholds=None):
    """Decide what to do about a drift (s, positive = ahead).

    Returns ("noop",), ("speed", x) or ("seek",). For a seek the caller seeks to the
    current target_position (it knows `now`; we don't).
    """
    t = {**DEFAULTS, **(thresholds or {})}
    a = abs(drift)
    if a < t["deadband"]:
        return ("noop",)
    if a > t["seek_threshold"]:
        return ("seek",)
    nudge = max(-t["max_nudge"], min(t["max_nudge"], t["gain"] * drift))
    return ("speed", round(1.0 - nudge, 4))


def _selfcheck():
    # target position, incl. loop wrap and epoch offset
    assert target_position(100.0, 0, 60) == 40.0
    assert target_position(120.0, 0, 60) == 0.0
    assert abs(target_position(119.96, 0, 60) - 59.96) < 1e-9
    assert target_position(1000.0, 990.0, 60) == 10.0
    assert target_position(980.0, 990.0, 60) == 50.0  # now before epoch still works

    # drift across the loop boundary: target just wrapped to 0.02, player still at 59.98
    assert abs(drift(59.98, 0.02, 60) - (-0.04)) < 1e-9
    assert abs(drift(0.02, 59.98, 60) - 0.04) < 1e-9
    assert abs(drift(10.3, 10.0, 60) - 0.3) < 1e-9
    assert abs(drift(9.7, 10.0, 60) - (-0.3)) < 1e-9

    # each threshold branch
    assert correction(0.0) == ("noop",)
    assert correction(0.019) == ("noop",)
    assert correction(-0.019) == ("noop",)
    assert correction(0.6) == ("seek",)
    assert correction(-0.6) == ("seek",)
    assert correction(0.03) == ("speed", 0.985)   # ahead -> slow down
    assert correction(-0.03) == ("speed", 1.015)  # behind -> speed up
    assert correction(0.4) == ("speed", 0.98)   # clamped
    assert correction(-0.4) == ("speed", 1.02)  # clamped
    # custom thresholds
    assert correction(0.1, {"seek_threshold": 0.05}) == ("seek",)
    assert correction(0.1, {"deadband": 0.2}) == ("noop",)
    print("sync self-check OK")


if __name__ == "__main__":
    _selfcheck()
