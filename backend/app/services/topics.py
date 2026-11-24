"""Topic status transitions — the one place where a status change fans out to the rest of the
system (history, revision schedule, roadmap steps)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import now
from ..enums import TOPIC_DONE_STATUSES
from ..models import ReviewItem, RoadmapItem, RoadmapStep, Topic


class MasteryEvidenceError(ValueError):
    """Raised when a topic is marked learned/mastered without demonstrated understanding."""


def check_evidence(db: Session, topic: Topic, new_status: str) -> None:
    """Reading a topic is not enough: 'learned' needs a passed assessment (or a solved linked
    exercise when the topic has no questions); 'mastered' needs a strong score plus revisions."""
    from ..enums import SOLVED_OUTCOMES
    from ..models import Exercise, ExerciseAttempt
    from . import assessment

    if new_status == "learned":
        if assessment.has_passed(db, topic.id):
            return
        solved = db.scalar(
            select(ExerciseAttempt.id).join(Exercise)
            .where(Exercise.topics.any(Topic.id == topic.id), ExerciseAttempt.outcome.in_(SOLVED_OUTCOMES))
        )
        if solved or not topic.questions:
            return
        raise MasteryEvidenceError(
            f"Pass the assessment for '{topic.title}' (≥{assessment.PASS_PCT:.0f}%) to mark it learned — "
            "reading it isn't enough.")
    if new_status == "mastered":
        ok, why = assessment.mastery_evidence(db, topic)
        if not ok:
            raise MasteryEvidenceError(f"'{topic.title}' can't be mastered yet: {why}.")


def set_status(db: Session, topic: Topic, new_status: str, reason: str = "", *, require_evidence: bool = False) -> bool:
    """Change a topic's status with all side effects. Returns True if it changed.
    Manual changes from the UI pass require_evidence=True."""
    old = topic.status
    if old == new_status:
        return False
    if require_evidence and new_status in TOPIC_DONE_STATUSES:
        check_evidence(db, topic, new_status)
    topic.status = new_status
    ts = now()
    if new_status != "not_started" and topic.started_at is None:
        topic.started_at = ts
    if new_status in TOPIC_DONE_STATUSES and topic.completed_at is None:
        topic.completed_at = ts
    history.record(
        db, "status_change", "topic", topic.id,
        f"{topic.title}: {old.replace('_', ' ')} → {new_status.replace('_', ' ')}" + (f" ({reason})" if reason else ""),
        skill_id=topic.skill_id, topic_id=topic.id, field="status", old_value=old, new_value=new_status,
    )
    if new_status in TOPIC_DONE_STATUSES:
        _ensure_revision(db, topic)
        _complete_roadmap_steps(db, topic)
    return True


def _ensure_revision(db: Session, topic: Topic) -> None:
    """Learning a topic starts its spaced-repetition schedule (Day 1, 3, 7, 14, 30…)."""
    from . import revision  # local import avoids a cycle

    exists = db.scalar(select(ReviewItem.id).where(ReviewItem.item_type == "topic", ReviewItem.item_id == topic.id))
    if not exists:
        revision.schedule(db, "topic", topic.id)


def _complete_roadmap_steps(db: Session, topic: Topic) -> None:
    for step in db.scalars(select(RoadmapStep).where(RoadmapStep.topic_id == topic.id, RoadmapStep.done.is_(False))):
        step.done = True
        step.done_at = now()
        item = db.get(RoadmapItem, step.item_id)
        if item and item.status == "not_started":
            item.status = "in_progress"
            item.started_at = item.started_at or now()
        history.record(
            db, "roadmap_step_done", "roadmap", step.item_id,
            f"Roadmap step done: {step.title} (topic learned)", skill_id=topic.skill_id, topic_id=topic.id,
        )
