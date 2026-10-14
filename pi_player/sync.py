"""Clock-based sync math. Pure functions; self-check: python3 -m pi_player.sync

Every screen shows position (now - epoch) mod duration. GStreamer renders a frame at
base_time + running_time, and running time restarts at 0 on the position we seek to, so a
player is on the group clock iff (base_time - epoch) mod duration == that position. Looping
with non-flushing segment seeks adds exactly one duration of running time per loop, so one
correct base time keeps a screen locked forever: no drift measuring or speed nudging needed.
"""


def target_position(now, epoch, duration):
    """Where every screen in the group should be right now (seconds into the video)."""
    return (now - epoch) % duration


def drift(actual, target, duration):
    """actual - target, wrapped to [-duration/2, duration/2) so a loop wrap isn't a huge drift."""
    d = (actual - target) % duration
    return d - duration if d >= duration / 2 else d


def base_time_ns(epoch, duration, position, not_before):
    """Earliest clock time >= not_before at which media `position` is due (all ints, ns)."""
    k = -((epoch + position - not_before) // duration)  # ceil((not_before - epoch - position) / duration)
    return epoch + position + k * duration


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

    # base time: the position is due exactly on the shared timeline, at or after not_before
    assert base_time_ns(0, 60, 10, 0) == 10
    assert base_time_ns(0, 60, 10, 10) == 10   # exactly on time is fine
    assert base_time_ns(0, 60, 10, 11) == 70   # missed it -> next loop
    assert base_time_ns(0, 60, 10, 125) == 130
    assert base_time_ns(1000, 60, 0, 990) == 1000   # not_before ahead of the epoch
    assert base_time_ns(1000, 60, 5, 900) == 945    # before the epoch: still on the timeline
    for nb in (0, 7, 59, 60, 61, 10**12 + 3):
        b = base_time_ns(17, 60, 23, nb)
        assert b >= nb and b - nb < 60 and target_position(b, 17, 60) == 23
    print("sync self-check OK")


if __name__ == "__main__":
    _selfcheck()
