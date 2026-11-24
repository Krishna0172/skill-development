"""Weekly and monthly learning reports generated from recorded activity, plus CSV export."""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import now
from ..enums import SOLVED_OUTCOMES
from ..models import (
    Exercise,
    ExerciseAttempt,
    Goal,
    HistoryEvent,
    LearningSession,
    Project,
    ProjectItem,
    ReviewItem,
    ReviewLog,
    Skill,
    Topic,
)
from . import stats, timecalc


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _core(db: Session, start: date, end: date) -> dict[str, Any]:
    n = now()
    w = _window(start, end)
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    topics = {t.id: t for t in db.scalars(select(Topic))}
    sessions = list(db.scalars(select(LearningSession).where(
        LearningSession.started_at < w[1],
        (LearningSession.ended_at.is_(None)) | (LearningSession.ended_at > w[0]))))
    reviews = list(db.scalars(select(ReviewLog).where(ReviewLog.reviewed_at >= w[0], ReviewLog.reviewed_at < w[1])))
    attempts = list(db.scalars(select(ExerciseAttempt).where(
        ExerciseAttempt.submitted_at >= w[0], ExerciseAttempt.submitted_at < w[1])))
    exercises = {e.id: e for e in db.scalars(select(Exercise))}
    status_events = list(db.scalars(select(HistoryEvent).where(
        HistoryEvent.occurred_at >= w[0], HistoryEvent.occurred_at < w[1],
        HistoryEvent.field.in_(["status", "proficiency"]))))

    time_by_skill = timecalc.seconds_by_key(sessions, lambda s: s.skill_id, w, n)
    learned = [t for t in topics.values() if t.completed_at and w[0] <= t.completed_at < w[1]]
    studied_topics = {s.topic_id for s in sessions if s.topic_id} | {
        e.topic_id for e in status_events if e.topic_id and e.new_value in {"learning", "practised"}}
    revised = Counter(r.topic_id for r in reviews if r.topic_id)
    forgot = Counter(r.topic_id for r in reviews if r.topic_id and r.rating in {"forgot", "difficult"})
    solved_ids = {a.exercise_id for a in attempts if a.outcome in SOLVED_OUTCOMES}
    failed_ids = {a.exercise_id for a in attempts} - solved_ids
    prof_changes = [
        {"skill": skills.get(e.entity_id, "?"), "from": e.old_value, "to": e.new_value}
        for e in status_events if e.entity_type == "skill" and e.field == "proficiency"
    ]
    weak_concepts = Counter(c for ex_id in failed_ids if ex_id in exercises for c in exercises[ex_id].concepts[:3])
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": stats.period_totals(db, start, end),
        "time_by_skill": sorted(
            [{"skill": skills.get(k, "Unassigned"), "hours": round(v / 3600, 2)} for k, v in time_by_skill.items() if v],
            key=lambda r: -r["hours"]),
        "learned": [{"id": t.id, "title": t.title, "skill": skills.get(t.skill_id)} for t in learned],
        "studied": [{"id": tid, "title": topics[tid].title} for tid in studied_topics if tid in topics],
        "revised": [{"id": tid, "title": topics[tid].title, "times": c} for tid, c in revised.most_common() if tid in topics],
        "solved": [{"id": i, "title": exercises[i].title, "difficulty": exercises[i].difficulty} for i in solved_ids if i in exercises],
        "unsolved_attempts": [{"id": i, "title": exercises[i].title} for i in failed_ids if i in exercises],
        "skills_improved": prof_changes,
        "weak_areas": {
            "topics": [{"id": tid, "title": topics[tid].title, "misses": c} for tid, c in forgot.most_common(8) if tid in topics],
            "concepts": [{"concept": k, "count": v} for k, v in weak_concepts.most_common(8)],
        },
        "revision_performance": {
            "total": len(reviews),
            "remembered": sum(r.remembered for r in reviews),
            "rate": round(100 * sum(r.remembered for r in reviews) / len(reviews), 1) if reviews else None,
            "by_rating": dict(Counter(r.rating for r in reviews)),
        },
    }


def weekly(db: Session, week_start: date) -> dict[str, Any]:
    week_start -= timedelta(days=week_start.weekday())
    end = week_start + timedelta(days=6)
    report = _core(db, week_start, end)
    today = date.today()
    pending = list(db.scalars(select(Topic).where(Topic.status.in_(["learning", "practised", "needs_revision", "difficult"]))
                              .order_by(Topic.updated_at.desc()).limit(10)))
    upcoming_due = list(db.scalars(select(ReviewItem).where(
        ReviewItem.is_active.is_(True), ReviewItem.due_date <= end + timedelta(days=7)).order_by(ReviewItem.due_date)))
    goals = list(db.scalars(select(Goal).where(Goal.status.in_(["not_started", "in_progress"]))))
    near_goals = [g for g in goals if g.target_date and g.target_date <= end + timedelta(days=14)]
    priorities: list[str] = []
    if report["weak_areas"]["topics"]:
        priorities.append("Re-study: " + ", ".join(t["title"] for t in report["weak_areas"]["topics"][:3]))
    if upcoming_due:
        priorities.append(f"Clear {len(upcoming_due)} revisions due by next week")
    if report["unsolved_attempts"]:
        priorities.append("Retry: " + ", ".join(e["title"] for e in report["unsolved_attempts"][:3]))
    for g in sorted(near_goals, key=lambda g: g.target_date)[:3]:
        priorities.append(f"Goal due {g.target_date.isoformat()}: {g.title}")
    if pending:
        priorities.append("Continue: " + ", ".join(t.title for t in pending[:3]))
    prev = stats.period_totals(db, week_start - timedelta(days=7), week_start - timedelta(days=1))
    return {
        "type": "weekly", **report,
        "pending_topics": [{"id": t.id, "title": t.title, "status": t.status} for t in pending],
        "next_week_priorities": priorities,
        "previous": prev,
        "is_current": week_start <= today <= end,
    }


def monthly(db: Session, year: int, month: int) -> dict[str, Any]:
    start = date(year, month, 1)
    end = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    report = _core(db, start, end)
    w = _window(start, end)
    prev_end = start - timedelta(days=1)
    prev = stats.period_totals(db, prev_end.replace(day=1), prev_end)
    goals_done = list(db.scalars(select(Goal).where(Goal.completed_at >= w[0], Goal.completed_at < w[1])))
    items_done = list(db.execute(
        select(ProjectItem.title, Project.name).join(Project).where(
            ProjectItem.completed_at >= w[0], ProjectItem.completed_at < w[1])).all())
    by_project: dict[str, list[str]] = defaultdict(list)
    for title, pname in items_done:
        by_project[pname].append(title)
    cur = report["totals"]

    def delta(key: str) -> dict[str, Any]:
        a, b = cur.get(key, 0) or 0, prev.get(key, 0) or 0
        return {"current": a, "previous": b, "change": round(a - b, 2)}

    attention = list(db.scalars(select(Topic).where(Topic.status.in_(["needs_revision", "difficult"])).limit(10)))
    return {
        "type": "monthly", **report,
        "goals_achieved": [{"id": g.id, "title": g.title} for g in goals_done],
        "projects_progressed": [{"project": k, "items": v} for k, v in by_project.items()],
        "areas_needing_improvement": [{"id": t.id, "title": t.title, "status": t.status} for t in attention],
        "comparison": {k: delta(k) for k in ("hours", "topics_learned", "exercises_solved", "reviews", "active_days")},
        "previous": prev,
    }


def to_csv(report: dict[str, Any]) -> str:
    """Flatten a report into a sectioned CSV (one table per section)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"DevGrowth {report['type']} report", report["period"]["start"], report["period"]["end"]])
    w.writerow([])
    w.writerow(["Totals"])
    for k, v in report["totals"].items():
        w.writerow([k, v])

    def section(title: str, rows: list[dict[str, Any]]) -> None:
        w.writerow([])
        w.writerow([title])
        if not rows:
            w.writerow(["(none)"])
            return
        keys = list(rows[0].keys())
        w.writerow(keys)
        for r in rows:
            w.writerow([r.get(k, "") if not isinstance(r.get(k), list) else "; ".join(map(str, r[k])) for k in keys])

    section("Time by skill", report["time_by_skill"])
    section("Topics learned", report["learned"])
    section("Topics revised", report["revised"])
    section("Exercises solved", report["solved"])
    section("Skill proficiency changes", report["skills_improved"])
    section("Weak topics", report["weak_areas"]["topics"])
    section("Weak concepts", report["weak_areas"]["concepts"])
    if report["type"] == "weekly":
        section("Next week priorities", [{"priority": p} for p in report["next_week_priorities"]])
    else:
        section("Goals achieved", report["goals_achieved"])
        section("Projects progressed", report["projects_progressed"])
        section("Comparison with previous month", [{"metric": k, **v} for k, v in report["comparison"].items()])
    return buf.getvalue()


def sessions_csv(db: Session, start: date, end: date) -> str:
    w = _window(start, end)
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    topics = dict(db.execute(select(Topic.id, Topic.title)).all())
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["id", "date", "started_at", "ended_at", "minutes", "kind", "skill", "topic", "title", "source", "notes", "problems"])
    n = now()
    for s in db.scalars(select(LearningSession).where(LearningSession.started_at >= w[0], LearningSession.started_at < w[1])
                        .order_by(LearningSession.started_at)):
        end_ = s.ended_at or n
        out.writerow([s.id, s.started_at.date().isoformat(), s.started_at.isoformat(), s.ended_at.isoformat() if s.ended_at else "",
                      round((end_ - s.started_at).total_seconds() / 60, 1), s.kind, skills.get(s.skill_id, ""),
                      topics.get(s.topic_id, ""), s.title, s.source, s.notes, s.problems])
    return buf.getvalue()
