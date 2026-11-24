"""Time accounting that never double-counts overlapping sessions.

Sessions are clipped to the requested window, split at midnight so each day gets its own
share, and then merged (interval union) before summing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta
from typing import Any

Interval = tuple[datetime, datetime]


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Union of intervals (sorted, overlapping/touching ones merged)."""
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def total_seconds(intervals: Iterable[Interval]) -> int:
    return int(sum((e - s).total_seconds() for s, e in merge(intervals)))


def split_by_day(start: datetime, end: datetime) -> list[tuple[date, Interval]]:
    """Split an interval at midnight boundaries."""
    parts: list[tuple[date, Interval]] = []
    cur = start
    while cur < end:
        next_midnight = datetime.combine(cur.date() + timedelta(days=1), time.min)
        seg_end = min(end, next_midnight)
        parts.append((cur.date(), (cur, seg_end)))
        cur = seg_end
    return parts


def clip(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> Interval | None:
    s, e = max(start, window_start), min(end, window_end)
    return (s, e) if e > s else None


def session_interval(session: Any, now_: datetime) -> Interval:
    """(start, end) of a session object; running timers count up to now."""
    return session.started_at, session.ended_at or now_


def seconds_by_day(sessions: Iterable[Any], start_day: date, end_day: date, now_: datetime) -> dict[date, int]:
    """Union-merged seconds per calendar day within [start_day, end_day]."""
    w_start = datetime.combine(start_day, time.min)
    w_end = datetime.combine(end_day + timedelta(days=1), time.min)
    per_day: dict[date, list[Interval]] = defaultdict(list)
    for s in sessions:
        iv = clip(*session_interval(s, now_), w_start, w_end)
        if iv:
            for d, part in split_by_day(*iv):
                per_day[d].append(part)
    return {d: total_seconds(ivs) for d, ivs in per_day.items()}


def seconds_by_key(
    sessions: Iterable[Any],
    key: Callable[[Any], Any],
    window: tuple[datetime, datetime] | None,
    now_: datetime,
) -> dict[Any, int]:
    """Union-merged seconds grouped by key (e.g. skill_id). Overlaps are merged within a
    group; a session counted for two different skills legitimately counts for both."""
    groups: dict[Any, list[Interval]] = defaultdict(list)
    for s in sessions:
        iv = session_interval(s, now_)
        if window:
            iv = clip(*iv, *window)
            if not iv:
                continue
        groups[key(s)].append(iv)
    return {k: total_seconds(v) for k, v in groups.items()}


def find_overlaps(sessions: Iterable[Any], start: datetime, end: datetime, now_: datetime, exclude_id: int | None = None) -> list[Any]:
    return [
        s for s in sessions
        if s.id != exclude_id and clip(*session_interval(s, now_), start, end) is not None
    ]
