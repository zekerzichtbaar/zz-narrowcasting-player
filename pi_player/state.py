"""On-screen state machine: which of the four screens to show. Pure logic + self-check.

Self-check: python3 -m pi_player.state
"""
WIFI_SETUP, PAIRING, WAITING, PLAYING = "WIFI_SETUP", "PAIRING", "WAITING", "PLAYING"

# Offline this long while NOT playing -> wifi setup (rides out short blips without flicker).
OFFLINE_GRACE = 30.0


def decide(network_ok, has_token, has_content, offline_since, now, timeout, grace=OFFLINE_GRACE):
    """The state to show.

    offline_since: when the network was last seen going away (None while online).
    Offline policy: with token + content we keep PLAYING for `timeout` seconds (also right after a
    boot without network), then WIFI_SETUP. Pairing/waiting screens are useless offline, so they
    give way to WIFI_SETUP after a short grace. Network back -> the normal state, i.e. resume.
    """
    if not network_ok:
        away = now - offline_since if offline_since is not None else float("inf")
        if has_token and has_content and away < timeout:
            return PLAYING
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


def _selfcheck():
    T = 600.0
    # online: every (token, content) combo; offline_since/now irrelevant
    for content in (False, True):
        assert decide(True, False, content, None, 0, T) == PAIRING
    assert decide(True, True, False, None, 0, T) == WAITING
    assert decide(True, True, True, None, 0, T) == PLAYING
    # offline, long enough: always WIFI_SETUP
    for token in (False, True):
        for content in (False, True):
            assert decide(False, token, content, 0.0, 10_000.0, T) == WIFI_SETUP, (token, content)
            assert decide(False, token, content, None, 0.0, T) == WIFI_SETUP  # never seen online
    # offline briefly: playing keeps playing; others keep their screen during the grace period
    assert decide(False, True, True, 100.0, 100.0 + T - 1, T) == PLAYING
    assert decide(False, True, True, 100.0, 100.0 + T, T) == WIFI_SETUP
    assert decide(False, False, False, 100.0, 110.0, T) == PAIRING
    assert decide(False, True, False, 100.0, 110.0, T) == WAITING
    assert decide(False, True, False, 100.0, 100.0 + OFFLINE_GRACE, T) == WIFI_SETUP
    assert decide(False, False, True, 100.0, 100.0 + OFFLINE_GRACE, T) == WIFI_SETUP

    # timer walk-through: playing, network lost, 10 min later wifi setup, network back -> resume
    tm = OfflineTimer(0.0)
    seq = []
    for now, net in ((0, True), (10, False), (300, False), (609, False), (610, False), (700, False),
                     (701, True), (702, True)):
        seq.append(decide(net, True, True, tm.update(net, now), now, T))
    assert seq == [PLAYING, PLAYING, PLAYING, PLAYING, WIFI_SETUP, WIFI_SETUP, PLAYING, PLAYING], seq
    # a blip resets the timer
    tm = OfflineTimer(0.0)
    tm.update(False, 10)
    tm.update(True, 500)
    assert decide(False, True, True, tm.update(False, 600), 1100, T) == PLAYING
    # booted offline: timer starts at boot, so a paired Pi with content plays for `timeout`
    tm = OfflineTimer(50.0, online=False)
    assert decide(False, True, True, tm.update(False, 60), 60, T) == PLAYING
    assert decide(False, True, True, tm.update(False, 650), 650, T) == WIFI_SETUP
    print("state self-check OK")


if __name__ == "__main__":
    _selfcheck()
