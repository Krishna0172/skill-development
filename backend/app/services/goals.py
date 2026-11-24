"""Goal progress, computed from records so it can never drift from reality.

metric              counts since `count_from` (default: goal creation date)
manual              the manual_progress slider
exercises_solved    distinct exercises first solved (filters: skill_id, metric_filter.category)
topics_learned      topics reaching learned/mastered (filters: skill_id, metric_filter.category)
hours               learning hours, overlap-merged (filter: skill_id)
reviews_done        revision answers (filter: skill_id)
roadmap_item        % of steps done in metric_filter.roadmap_item_id
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import now
from ..enums import SOLVED_OUTCOMES, TOPIC_DONE_STATUSES
from ..models import Exercise, ExerciseAttempt, Goal, LearningSession, ReviewLog, RoadmapItem, Topic
from . import timecalc

METRIC_UNITS = {
    "exercises_solved": "exercises",
    "topics_learned": "topics",
    "hours": "hours",
    "reviews_done": "revisions",
    "roadmap_item": "% of steps",
    "manual": "%",
}


def _since(goal: Goal) -> datetime:
    start = goal.count_from or (goal.created_at.date() if goal.created_at else date.today())
    return datetime.combine(start, time.min)


def compute(db: Session, goal: Goal) -> dict[str, Any]:
    metric = goal.metric
    since = _since(goal)
    flt = goal.metric_filter or {}
    current: float
    target = goal.metric_target

    if metric == "exercises_solved":
        q = select(ExerciseAttempt.exercise_id, ExerciseAttempt.submitted_at).join(Exercise).where(
            ExerciseAttempt.outcome.in_(SOLVED_OUTCOMES))
        if goal.skill_id:
            q = q.where(Exercise.skill_id == goal.skill_id)
        if flt.get("category"):
            q = q.where(Exercise.category == flt["category"])
        first: dict[int, datetime] = {}
        for ex_id, ts in db.execute(q):
            first[ex_id] = min(ts, first.get(ex_id, ts))
        current = sum(1 for ts in first.values() if ts >= since)
    elif metric == "topics_learned":
        q = select(Topic).where(Topic.status.in_(TOPIC_DONE_STATUSES), Topic.completed_at >= since)
        if flt.get("category"):
            q = q.where(Topic.category == flt["category"])
        topics = list(db.scalars(q))
        if goal.skill_id:
            topics = [t for t in topics if t.skill_id == goal.skill_id or any(s.id == goal.skill_id for s in t.extra_skills)]
        current = len(topics)
    elif metric == "hours":
        q = select(LearningSession).where((LearningSession.ended_at.is_(None)) | (LearningSession.ended_at > since))
        if goal.skill_id:
            q = q.where(LearningSession.skill_id == goal.skill_id)
        n = now()
        ivs = [iv for s in db.scalars(q) if (iv := timecalc.clip(*timecalc.session_interval(s, n), since, n))]
        current = round(timecalc.total_seconds(ivs) / 3600, 2)
    elif metric == "reviews_done":
        q = select(ReviewLog.id).where(ReviewLog.reviewed_at >= since)
        if goal.skill_id:
            q = q.where(ReviewLog.skill_id == goal.skill_id)
        current = len(db.execute(q).all())
    elif metric == "roadmap_item":
        item = db.get(RoadmapItem, flt.get("roadmap_item_id") or 0)
        steps = item.steps if item else []
        current = round(100 * sum(s.done for s in steps) / len(steps), 1) if steps else 0
        target = 100
    else:
        current = goal.manual_progress
        target = 100

    percent = 0.0 if not target else min(100.0, round(100 * current / target, 1))
    today = date.today()
    open_ = goal.status in {"not_started", "in_progress"}
    return {
        "current": current,
        "target": target,
        "unit": METRIC_UNITS.get(metric, ""),
        "percent": percent,
        "target_reached": percent >= 100,
        "overdue": bool(open_ and goal.target_date and goal.target_date < today),
        "days_left": (goal.target_date - today).days if goal.target_date else None,
    }
