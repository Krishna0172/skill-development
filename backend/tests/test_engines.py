"""Unit tests for the pure engines: spaced repetition and time accounting."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import revision, timecalc


@pytest.mark.parametrize(
    ("stage", "rating", "expected_stage", "expected_days", "remembered", "lapse"),
    [
        (0, "easy", 1, 3, True, False),           # Day 1 → Day 3
        (1, "easy", 2, 7, True, False),           # → Day 7
        (2, "easy", 3, 14, True, False),          # → Day 14
        (3, "easy", 4, 30, True, False),          # → Day 30
        (6, "easy", 6, 120, True, False),         # capped at the top of the ladder
        (3, "need_practice", 3, 14, True, False),  # repeat the same interval
        (3, "difficult", 2, 7, False, False),     # step back one rung
        (0, "difficult", 0, 1, False, False),     # can't go below Day 1
        (5, "forgot", 0, 1, False, True),         # reset to Day 1, count a lapse
    ],
)
def test_next_state_ladder(stage, rating, expected_stage, expected_days, remembered, lapse):
    t = revision.next_state(stage, rating)
    assert (t.stage, t.interval_days, t.remembered, t.lapse) == (expected_stage, expected_days, remembered, lapse)


def test_next_state_rejects_unknown_rating():
    with pytest.raises(ValueError):
        revision.next_state(0, "meh")


def dt(h, m=0, day=1):
    return datetime(2026, 1, day, h, m)


def test_merge_overlapping_and_touching_intervals():
    merged = timecalc.merge([(dt(10), dt(11)), (dt(10, 30), dt(12)), (dt(12), dt(13)), (dt(15), dt(16))])
    assert merged == [(dt(10), dt(13)), (dt(15), dt(16))]


def test_total_seconds_never_double_counts():
    # two fully overlapping 1-hour sessions = 1 hour, not 2
    assert timecalc.total_seconds([(dt(9), dt(10)), (dt(9), dt(10))]) == 3600
    assert timecalc.total_seconds([(dt(9), dt(10)), (dt(9, 30), dt(10, 30))]) == 5400


def test_split_by_day_at_midnight():
    parts = timecalc.split_by_day(datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 2, 1, 0))
    assert [(d, (b - a).seconds) for d, (a, b) in parts] == [(date(2026, 1, 1), 3600), (date(2026, 1, 2), 3600)]


def test_seconds_by_day_merges_within_day_and_clips_window():
    S = lambda a, b: SimpleNamespace(id=None, started_at=a, ended_at=b)  # noqa: E731
    sessions = [S(dt(9), dt(10)), S(dt(9, 30), dt(10, 30)), S(datetime(2025, 12, 31, 23), dt(0, 30))]
    by_day = timecalc.seconds_by_day(sessions, date(2026, 1, 1), date(2026, 1, 1), datetime(2026, 1, 2))
    assert by_day == {date(2026, 1, 1): 5400 + 1800}


def test_running_session_counts_until_now():
    s = SimpleNamespace(id=1, started_at=dt(9), ended_at=None)
    assert timecalc.session_interval(s, dt(9, 45)) == (dt(9), dt(9, 45))
    assert timecalc.find_overlaps([s], dt(9, 30), dt(10), dt(9, 45)) == [s]
    assert timecalc.find_overlaps([s], dt(10), dt(11), dt(9, 45)) == []


def test_ladder_matches_requirements():
    # Day 1, 3, 7, 14, 30 are the required intervals; longer ones extend them
    assert revision.LADDER[:5] == (1, 3, 7, 14, 30)
    assert all(b > a for a, b in zip(revision.LADDER, revision.LADDER[1:]))
    assert timedelta(days=revision.LADDER[0]) == timedelta(days=1)
