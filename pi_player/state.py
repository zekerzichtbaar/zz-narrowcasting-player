"""On-screen state machine: which of the four screens to show. Pure logic + self-check.

Self-check: python3 -m pi_player.state
"""
WIFI_SETUP, PAIRING, WAITING, PLAYING = "WIFI_SETUP", "PAIRING", "WAITING", "PLAYING"

# Offline this long -> wifi setup (rides out short blips, and lets NetworkManager join the saved
# wifi after a boot before the hotspot takes the radio).
OFFLINE_GRACE = 30.0


def decide(network_ok, linked, has_token, has_content, offline_since, now, setup_left, forced=False,
           grace=OFFLINE_GRACE):
    """The state to show.

    linked: NetworkManager is connected (hotspot excluded), whether or not the CMS answers.
    offline_since: when the network was last seen going away (None while online).
    setup_left: seconds left in the setup window after boot (SetupWindow), <= 0 = closed.
    forced: the window was opened from the CMS ("Wifi opnieuw instellen"): setup also while online.
    Offline policy: a paired Pi with content always plays. Only right after a boot without any
    connection it shows WIFI_SETUP first, for the setup window (the countdown), then plays. A
    wifi without internet or a CMS that is down never brings up the hotspot there: it can't help.
    Pairing/waiting screens are useless offline, so they give way to WIFI_SETUP after the grace.
    Network back -> the normal state.
    """
    if forced and setup_left > 0:
        return WIFI_SETUP
    if not network_ok:
        away = now - offline_since if offline_since is not None else float("inf")
        if has_token and has_content:
            return WIFI_SETUP if not linked and away >= grace and setup_left > 0 else PLAYING
        if away >= grace:
            return WIFI_SETUP
    if not has_token:
        return PAIRING
    return PLAYING if has_content else WAITING


class OfflineTimer:
    """Tracks offline_since from successive network_ok samples."""

    def __init__(self, now, online=True):
        self.since = None if online else now

    def update(self, network_ok, now):
        if network_ok:
            self.since = None
        elif self.since is None:
            self.since = now
        return self.since


class SetupWindow:
    """The wifi-setup countdown after a boot: open for `timeout` s from agent start, closed for good
    (until the next boot) once the network was seen. A restart is how someone asks for the setup.
    open(): the same countdown on request from the CMS, also while online (`forced`); it ends early
    only once a network was set up from the phone (`joined`), not when the old wifi answers."""

    def __init__(self, now, timeout):
        self.until = now + timeout
        self.forced = False

    def open(self, now, timeout):
        self.until, self.forced = now + timeout, True

    def update(self, network_ok, now, joined=False):
        if joined or (network_ok and not self.forced):
            self.until = 0.0
        if self.until <= now:
            self.forced = False
        return self.until - now

    def hold(self, now, keep):
        """A phone is on the hotspot: never let the countdown run out under it."""
        if self.until > 0:
            self.until = max(self.until, now + keep)


def _selfcheck():
    T = 600.0
    # online: every (token, content) combo; the rest is irrelevant
    for content in (False, True):
        assert decide(True, True, False, content, None, 0, T) == PAIRING
    assert decide(True, True, True, False, None, 0, T) == WAITING
    assert decide(True, True, True, True, None, 0, T) == PLAYING
    # offline without content or token: WIFI_SETUP after the grace, window or not
    for token, content in ((False, False), (False, True), (True, False)):
        for left in (T, 0):
            assert decide(False, False, token, content, 0.0, 10_000.0, left) == WIFI_SETUP, (token, content)
            assert decide(False, False, token, content, None, 0.0, left) == WIFI_SETUP  # never seen online
    assert decide(False, False, False, False, 100.0, 110.0, 0) == PAIRING  # within the grace
    assert decide(False, False, True, False, 100.0, 110.0, 0) == WAITING
    # paired with content: setup only inside the window and without any connection
    assert decide(False, False, True, True, 0.0, 10_000.0, 0) == PLAYING  # window closed: forever
    assert decide(False, False, True, True, 0.0, OFFLINE_GRACE, 1) == WIFI_SETUP
    assert decide(False, False, True, True, 0.0, OFFLINE_GRACE - 1, T) == PLAYING  # NM still joining
    assert decide(False, True, True, True, 0.0, 10_000.0, T) == PLAYING  # wifi up, CMS gone

    def walk(steps, linked=False, phone=None):
        tm, win, seen = OfflineTimer(0.0, online=False), SetupWindow(0.0, T), []
        for now, net in steps:
            if phone and phone(now):
                win.hold(now, 60)
            left = win.update(net, now)
            seen.append(decide(net, linked, True, True, tm.update(net, now), now, left))
        return seen
    # booted offline: setup after the grace, countdown runs out, then plays for good
    assert walk(((10, False), (30, False), (599, False), (600, False), (50_000, False))) == \
        [PLAYING, WIFI_SETUP, WIFI_SETUP, PLAYING, PLAYING]
    # booted with wifi but no CMS: plays straight away
    assert set(walk(((10, False), (30, False), (300, False)), linked=True)) == {PLAYING}
    # network back during the countdown: plays, and a later loss never brings setup back
    assert walk(((30, False), (200, True), (300, False), (500, False))) == [WIFI_SETUP, PLAYING] * 1 + [PLAYING] * 2
    # booted online, lost later: plays on
    assert set(walk(((5, True), (100, False), (400, False), (5_000, False)))) == {PLAYING}
    # a phone on the hotspot at the end holds the countdown
    assert walk(((30, False), (590, False), (650, False), (720, False)), phone=lambda t: t < 700) == \
        [WIFI_SETUP, WIFI_SETUP, WIFI_SETUP, PLAYING]
    # "Wifi opnieuw instellen" from the CMS: setup while online, old wifi answering keeps it,
    # a network set up from the phone or the countdown ends it
    win = SetupWindow(0.0, T)
    assert win.update(True, 1) <= 0  # booted online: closed
    win.open(100.0, T)
    assert win.forced and decide(True, True, True, True, None, 100.0, win.update(True, 100.0), win.forced) == WIFI_SETUP
    assert decide(True, True, True, True, None, 300.0, win.update(True, 300.0), win.forced) == WIFI_SETUP
    assert decide(True, True, True, True, None, 700.0, win.update(True, 700.0), win.forced) == PLAYING
    assert not win.forced
    win.open(1000.0, T)
    assert decide(True, True, True, True, None, 1100.0, win.update(True, 1100.0, joined=True), win.forced) == PLAYING
    assert decide(False, False, True, True, 1200.0, 1300.0, win.update(False, 1300.0), win.forced) == PLAYING
    print("state self-check OK")


if __name__ == "__main__":
    _selfcheck()
