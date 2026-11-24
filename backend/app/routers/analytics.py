"""Dashboard, daily workflow ("Today"), analytics and reports."""

from __future__ import annotations

import random
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import SOLVED_OUTCOMES
from ..models import DailyLog, Exercise, Goal, LearningSession, ReviewItem, RoadmapItem, Skill, Topic
from ..services import goals as goal_service
from ..services import reports, stats
from .common import iso

router = APIRouter(tags=["analytics"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return stats.dashboard(db)


@router.get("/today")
def today_plan(db: Session = Depends(get_db)):
    """The daily workflow: what to learn, revise, practise; deadlines; yesterday; this week."""
    today = date.today()
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())

    # 1. What to learn: topics in progress, then the next undone roadmap steps
    in_progress = list(db.scalars(select(Topic).where(Topic.status.in_(["learning", "practised"]))
                                  .order_by(Topic.updated_at.desc()).limit(4)))
    roadmap_items = list(db.scalars(select(RoadmapItem).where(RoadmapItem.status.in_(["in_progress", "not_started"]))
                                    .order_by(RoadmapItem.status != "in_progress", RoadmapItem.sort_order)))
    next_steps = []
    for item in roadmap_items:
        step = next((s for s in item.steps if not s.done), None)
        if step:
            topic = db.get(Topic, step.topic_id) if step.topic_id else None
            next_steps.append({"roadmap_id": item.id, "roadmap": item.title, "step_id": step.id, "step": step.title,
                               "topic_id": topic.id if topic else None, "topic_status": topic.status if topic else None,
                               "item_status": item.status})
        if len(next_steps) >= 4:
            break

    # 2. Revision
    due = list(db.scalars(select(ReviewItem).where(ReviewItem.is_active.is_(True), ReviewItem.due_date <= today)
                          .order_by(ReviewItem.due_date)))
    attention = list(db.scalars(select(Topic).where(Topic.status.in_(["needs_revision", "difficult"])).limit(5)))

    # 3. Coding: retry unsolved attempts, then pending exercises for active-roadmap / weak skills (stable per day)
    ex_status = stats._exercise_status(db)
    exercises = list(db.scalars(select(Exercise)))
    retry = [e for e in exercises if ex_status.get(e.id) == "attempted"][:2]
    focus_skills = {i.skill_id for i in roadmap_items if i.status == "in_progress" and i.skill_id}
    focus_skills |= {t.skill_id for t in attention if t.skill_id}
    pending = [e for e in exercises if e.id not in ex_status]
    rng = random.Random(today.toordinal())
    rng.shuffle(pending)
    order = {"easy": 0, "medium": 1, "hard": 2}
    pending.sort(key=lambda e: (e.skill_id not in focus_skills, order.get(e.difficulty, 3)))
    suggestions = [{"id": e.id, "title": e.title, "difficulty": e.difficulty, "category": e.category,
                    "skill": skills.get(e.skill_id), "reason": "Retry — not solved yet"} for e in retry]
    for e in pending[: max(0, 3 - len(suggestions))]:
        suggestions.append({"id": e.id, "title": e.title, "difficulty": e.difficulty, "category": e.category,
                            "skill": skills.get(e.skill_id),
                            "reason": "Matches your current focus" if e.skill_id in focus_skills else "New challenge"})

    # 4. Goals near deadline (or overdue)
    goals = []
    for g in db.scalars(select(Goal).where(Goal.status.in_(["not_started", "in_progress"]), Goal.target_date.is_not(None))):
        if g.target_date <= today + timedelta(days=14):
            goals.append({"id": g.id, "title": g.title, "target_date": iso(g.target_date), "priority": g.priority,
                          "progress": goal_service.compute(db, g)})
    goals.sort(key=lambda g: g["target_date"])

    # 5/6. Yesterday and this week
    week_start = today - timedelta(days=today.weekday())
    this_week = stats.period_totals(db, week_start, today)
    last_week = stats.period_totals(db, week_start - timedelta(days=7), week_start - timedelta(days=7 - today.weekday()))
    running = db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None)))
    return {
        "date": today.isoformat(),
        "learn": {"in_progress": [{"id": t.id, "title": t.title, "status": t.status, "skill": skills.get(t.skill_id)}
                                  for t in in_progress],
                  "next_steps": next_steps},
        "revision": {"due_count": len(due), "overdue_count": sum(1 for r in due if r.due_date < today),
                     "items": [stats._review_item_brief(db, r) for r in due[:6]],
                     "attention_topics": [{"id": t.id, "title": t.title, "status": t.status} for t in attention]},
        "practice": suggestions,
        "goals": goals,
        "yesterday": stats.day_summary(db, today - timedelta(days=1)),
        "today_so_far": stats.day_summary(db, today),
        "week": {"this": this_week, "last_same_point": last_week,
                 "days": [{"date": (week_start + timedelta(days=i)).isoformat(),
                           "minutes": stats.day_summary(db, week_start + timedelta(days=i))["total_minutes"]
                           if week_start + timedelta(days=i) <= today else None} for i in range(7)]},
        "streak": stats.streaks(db),
        "timer_running": running is not None,
        "has_log_today": db.scalar(select(DailyLog.id).where(DailyLog.log_date == today)) is not None,
        "unassessed_skills": len(db.scalars(select(Skill.id).where(Skill.proficiency.is_(None), Skill.is_archived.is_(False))).all()),
        "solved_total": sum(1 for v in ex_status.values() if v == "solved"),
        "solved_outcomes": sorted(SOLVED_OUTCOMES),
    }


@router.get("/analytics")
def analytics(range: str = "30d", start: date | None = None, end: date | None = None, db: Session = Depends(get_db)):
    try:
        s, e = stats.resolve_range(db, range, start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if (e - s).days > 3660:
        raise HTTPException(422, "Range too large (max 10 years)")
    return stats.analytics(db, s, e)


@router.get("/reports/weekly")
def weekly_report(week_start: date | None = None, format: str = "json", db: Session = Depends(get_db)):
    report = reports.weekly(db, week_start or date.today())
    if format == "csv":
        return Response(reports.to_csv(report), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="weekly-{report["period"]["start"]}.csv"'})
    return report


@router.get("/reports/monthly")
def monthly_report(month: str | None = None, format: str = "json", db: Session = Depends(get_db)):
    try:
        y, m = (int(x) for x in (month or date.today().strftime("%Y-%m")).split("-"))
        if not 1 <= m <= 12:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "month must be YYYY-MM") from exc
    report = reports.monthly(db, y, m)
    if format == "csv":
        return Response(reports.to_csv(report), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="monthly-{y:04d}-{m:02d}.csv"'})
    return report


@router.get("/reports/sessions.csv")
def sessions_csv(start: date | None = None, end: date | None = None, db: Session = Depends(get_db)):
    s, e = stats.resolve_range(db, "all") if not (start and end) else (start, end)
    return Response(reports.sessions_csv(db, s, e), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="sessions-{s}-{e}.csv"'})
