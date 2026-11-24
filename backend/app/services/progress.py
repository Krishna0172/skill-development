"""Progress measures computed only from recorded evidence (assessments, revisions, practice).

- Area progress: share of curriculum topics in an area whose understanding has been
  DEMONSTRATED (passed assessment), plus mastered count and best average score.
- Role readiness: transparent weighted mix of area progress + interview practice confidence.
- Targets: daily / weekly / monthly goals vs what was actually recorded in that window.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import now
from ..enums import ROLES, SOLVED_OUTCOMES
from ..models import (
    AssessmentAttempt,
    ExerciseAttempt,
    InterviewQuestion,
    LearningSession,
    ReviewLog,
    RoadmapItem,
    Skill,
    Target,
    Topic,
)
from . import timecalc

AREAS = {
    "python": "Python",
    "postgresql": "PostgreSQL & databases",
    "backend": "Backend engineering",
    "aws": "AWS & infrastructure",
    "ai": "AI Engineering",
}

ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "python-dev": {"python": 0.6, "postgresql": 0.1, "backend": 0.1, "interview": 0.2},
    "python-backend": {"python": 0.35, "backend": 0.25, "postgresql": 0.2, "interview": 0.2},
    "backend-engineer": {"backend": 0.3, "postgresql": 0.3, "aws": 0.2, "interview": 0.2},
    "ai-engineer": {"ai": 0.5, "python": 0.2, "backend": 0.1, "interview": 0.2},
    "genai-engineer": {"ai": 0.55, "python": 0.15, "backend": 0.1, "interview": 0.2},
}


def _best_scores(db: Session) -> dict[int, float]:
    out: dict[int, float] = {}
    for topic_id, pct in db.execute(select(AssessmentAttempt.topic_id, AssessmentAttempt.score_pct).where(
            AssessmentAttempt.mode == "assessment", AssessmentAttempt.topic_id.is_not(None))):
        out[topic_id] = max(pct, out.get(topic_id, 0.0))
    return out


def area_topics(db: Session) -> dict[str, list[int]]:
    """Topic ids per area, taken from the curriculum (roadmap items carry their area)."""
    areas: dict[str, set[int]] = {k: set() for k in AREAS}
    for item in db.scalars(select(RoadmapItem).where(RoadmapItem.area.is_not(None))):
        if item.area in areas:
            areas[item.area].update(s.topic_id for s in item.steps if s.topic_id)
    return {k: sorted(v) for k, v in areas.items()}


def area_progress(db: Session) -> list[dict[str, Any]]:
    best = _best_scores(db)
    topics = {t.id: t for t in db.scalars(select(Topic))}
    skills = list(db.scalars(select(Skill).where(Skill.is_archived.is_(False))))
    track_of_area = {"python": "python", "postgresql": "backend", "backend": "backend", "aws": "backend", "ai": "ai"}
    out = []
    for key, ids in area_topics(db).items():
        ids = [i for i in ids if i in topics]
        total = len(ids)
        assessed = [i for i in ids if i in best]
        passed = [i for i in ids if topics[i].status in {"learned", "mastered"}]
        mastered = [i for i in ids if topics[i].status == "mastered"]
        rated = [s.proficiency for s in skills if s.track == track_of_area[key] and s.proficiency]
        out.append({
            "area": key, "label": AREAS[key], "topics": total,
            "assessed": len(assessed), "passed": len(passed), "mastered": len(mastered),
            "percent": round(100 * len(passed) / total, 1) if total else 0.0,
            "avg_best_score": round(sum(best[i] for i in assessed) / len(assessed), 1) if assessed else None,
            "self_rated_avg": round(sum(rated) / len(rated), 1) if rated else None,
            "attention": sum(1 for i in ids if topics[i].status in {"needs_revision", "difficult"}),
        })
    return out


def role_readiness(db: Session) -> list[dict[str, Any]]:
    areas = {a["area"]: a["percent"] for a in area_progress(db)}
    questions = list(db.scalars(select(InterviewQuestion)))
    out = []
    for role, weights in ROLE_WEIGHTS.items():
        role_qs = [q for q in questions if role in (q.roles or [])] or [q for q in questions if not q.roles]
        confident = sum(1 for q in role_qs if (q.confidence or 0) >= 4)
        interview_pct = round(100 * confident / len(role_qs), 1) if role_qs else 0.0
        parts = []
        score = 0.0
        for comp, w in weights.items():
            value = interview_pct if comp == "interview" else areas.get(comp, 0.0)
            parts.append({"component": "Interview practice" if comp == "interview" else AREAS[comp],
                          "weight": w, "value": value})
            score += w * value
        out.append({"role": role, "label": ROLES[role], "score": round(score, 1), "components": parts,
                    "interview_questions": len(role_qs), "interview_confident": confident})
    return sorted(out, key=lambda r: -r["score"])


# --------------------------------------------------------------------------- targets


def period_window(period: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if period == "daily":
        return today, today
    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    start = today.replace(day=1)
    nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def measure(db: Session, metric: str, start: date, end: date) -> float:
    w0, w1 = datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)
    if metric == "minutes":
        n = now()
        sessions = db.scalars(select(LearningSession).where(
            LearningSession.started_at < w1, (LearningSession.ended_at.is_(None)) | (LearningSession.ended_at > w0)))
        ivs = [iv for s in sessions if (iv := timecalc.clip(*timecalc.session_interval(s, n), w0, w1))]
        return round(timecalc.total_seconds(ivs) / 60)
    if metric == "reviews":
        return db.scalar(select(func.count()).select_from(ReviewLog).where(
            ReviewLog.reviewed_at >= w0, ReviewLog.reviewed_at < w1)) or 0
    if metric == "exercises_solved":
        first: dict[int, datetime] = {}
        for ex_id, ts in db.execute(select(ExerciseAttempt.exercise_id, ExerciseAttempt.submitted_at).where(
                ExerciseAttempt.outcome.in_(SOLVED_OUTCOMES))):
            first[ex_id] = min(ts, first.get(ex_id, ts))
        return sum(1 for ts in first.values() if w0 <= ts < w1)
    if metric == "topics_passed":
        return len(set(db.scalars(select(AssessmentAttempt.topic_id).where(
            AssessmentAttempt.passed.is_(True), AssessmentAttempt.mode == "assessment",
            AssessmentAttempt.taken_at >= w0, AssessmentAttempt.taken_at < w1))))
    if metric == "active_days":
        from .stats import active_days
        return len({d for d in active_days(db, start) if start <= d <= end})
    raise ValueError(f"Unknown metric {metric}")


METRIC_LABELS = {"minutes": "minutes studied", "reviews": "revisions", "exercises_solved": "exercises solved",
                 "topics_passed": "topics passed", "active_days": "active days"}


def targets_progress(db: Session) -> list[dict[str, Any]]:
    out = []
    order = {"daily": 0, "weekly": 1, "monthly": 2}
    for t in sorted(db.scalars(select(Target).where(Target.is_active.is_(True))), key=lambda t: (order[t.period], t.metric)):
        start, end = period_window(t.period)
        current = measure(db, t.metric, start, end)
        out.append({"id": t.id, "period": t.period, "metric": t.metric, "label": METRIC_LABELS[t.metric],
                    "target": t.value, "current": current, "percent": min(100.0, round(100 * current / t.value, 1)),
                    "start": start.isoformat(), "end": end.isoformat()})
    return out
