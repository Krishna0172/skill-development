"""Spaced-repetition revision engine.

Ladder of intervals (days): 1 → 3 → 7 → 14 → 30 → 60 → 120.
A new item is due the day after it is scheduled ("Day 1"). Each answer moves it on the ladder:

| Rating         | Remembered | Stage change          | Next interval        |
|----------------|------------|-----------------------|----------------------|
| easy           | yes        | +1 (max 6)            | ladder[new stage]    |
| need_practice  | yes        | stays                 | ladder[stage]        |
| difficult      | partially  | −1 (min 0)            | ladder[new stage]    |
| forgot         | no         | reset to 0, lapse +1  | 1 day                |

Each item also carries an ease (default 2.5, range 1.3–3.0, SM-2 style). Easy answers raise it,
difficult/forgot lower it, and it scales every interval beyond day 1 (interval = ladder × ease/2.5),
so items you find hard come back sooner and items you find easy drift out further.

Topic status is kept in sync (and logged): forgot → needs_revision, difficult → difficult,
easy at stage ≥ MASTERED_STAGE → mastered, otherwise a struggling topic recovers to learned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import now
from ..models import Flashcard, InterviewQuestion, ReviewItem, ReviewLog, Topic

LADDER: tuple[int, ...] = (1, 3, 7, 14, 30, 60, 120)
MAX_STAGE = len(LADDER) - 1
MASTERED_STAGE = 5  # reaching the 60-day interval with an "easy" answer = mastered


DEFAULT_EASE = 2.5
MIN_EASE, MAX_EASE = 1.3, 3.0
EASE_DELTA = {"easy": 0.1, "need_practice": 0.0, "difficult": -0.15, "forgot": -0.2}


@dataclass(frozen=True)
class Transition:
    stage: int
    interval_days: int
    remembered: bool
    lapse: bool
    ease: float = DEFAULT_EASE


def _interval(stage: int, ease: float) -> int:
    base = LADDER[stage]
    return base if stage == 0 else max(1, round(base * ease / DEFAULT_EASE))


def next_state(stage: int, rating: str, ease: float = DEFAULT_EASE) -> Transition:
    """Pure function: where an item goes after a rating. Unit-tested in isolation."""
    stage = max(0, min(stage, MAX_STAGE))
    if rating not in EASE_DELTA:
        raise ValueError(f"Unknown rating: {rating}")
    ease = ease or DEFAULT_EASE  # the interval uses the ease BEFORE this answer; the answer adjusts the next one
    new_ease = round(min(MAX_EASE, max(MIN_EASE, ease + EASE_DELTA[rating])), 2)
    if rating == "easy":
        new = min(stage + 1, MAX_STAGE)
        return Transition(new, _interval(new, ease), True, False, new_ease)
    if rating == "need_practice":
        return Transition(stage, _interval(stage, ease), True, False, new_ease)
    if rating == "difficult":
        new = max(stage - 1, 0)
        return Transition(new, _interval(new, ease), False, False, new_ease)
    return Transition(0, LADDER[0], False, True, new_ease)  # forgot


def _resolve_links(db: Session, item_type: str, item_id: int) -> tuple[int | None, int | None, str]:
    """Return (topic_id, skill_id, label) for a reviewable item, or raise LookupError."""
    if item_type == "topic":
        topic = db.get(Topic, item_id)
        if not topic:
            raise LookupError("Topic not found")
        return topic.id, topic.skill_id, topic.title
    if item_type == "flashcard":
        card = db.get(Flashcard, item_id)
        if not card:
            raise LookupError("Flashcard not found")
        skill_id = card.skill_id or (card.topic.skill_id if card.topic else None)
        return card.topic_id, skill_id, card.front[:80]
    if item_type == "interview":
        q = db.get(InterviewQuestion, item_id)
        if not q:
            raise LookupError("Interview question not found")
        return q.topic_id, q.skill_id, q.question[:80]
    if item_type == "question":
        from ..models import Question

        q = db.get(Question, item_id)
        if not q:
            raise LookupError("Question not found")
        skill_id = q.skill_id or (q.topic.skill_id if q.topic else None)
        return q.topic_id, skill_id, q.prompt[:80]
    raise LookupError(f"Unknown item type {item_type}")


def schedule(db: Session, item_type: str, item_id: int, *, start: date | None = None) -> ReviewItem:
    """Add an item to the revision queue (idempotent; re-activates a paused item)."""
    topic_id, skill_id, label = _resolve_links(db, item_type, item_id)
    existing = db.scalar(
        select(ReviewItem).where(ReviewItem.item_type == item_type, ReviewItem.item_id == item_id)
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.due_date = (start or date.today()) + timedelta(days=LADDER[existing.stage])
        return existing
    item = ReviewItem(
        item_type=item_type,
        item_id=item_id,
        topic_id=topic_id,
        skill_id=skill_id,
        stage=0,
        interval_days=LADDER[0],
        due_date=(start or date.today()) + timedelta(days=LADDER[0]),
    )
    db.add(item)
    db.flush()
    history.record(
        db, "revision_scheduled", item_type, item_id, f"Added to revision: {label}",
        skill_id=skill_id, topic_id=topic_id,
    )
    return item


def _sync_topic_status(db: Session, topic: Topic, rating: str, stage_after: int) -> None:
    from . import topics as topic_service  # local import avoids a cycle

    new_status: str | None = None
    if rating == "forgot":
        new_status = "needs_revision"
    elif rating == "difficult":
        new_status = "difficult"
    elif rating == "easy" and stage_after >= MASTERED_STAGE:
        new_status = "mastered"
    elif topic.status in {"needs_revision", "difficult", "not_started", "learning", "practised"}:
        new_status = "learned"
    if new_status in {"learned", "mastered"}:
        try:  # self-rated recall alone never promotes a topic — it must also be demonstrated
            topic_service.check_evidence(db, topic, new_status)
        except topic_service.MasteryEvidenceError:
            return
    if new_status:
        topic_service.set_status(db, topic, new_status, f"revision: {rating.replace('_', ' ')}")


def apply_rating(
    db: Session,
    item: ReviewItem,
    rating: str,
    *,
    confidence: int | None = None,
    duration_seconds: int | None = None,
    note: str = "",
    client_ref: str | None = None,
    today: date | None = None,
) -> ReviewLog:
    today = today or date.today()
    t = next_state(item.stage, rating, item.ease or DEFAULT_EASE)
    log = ReviewLog(
        client_ref=client_ref,
        review_item_id=item.id,
        item_type=item.item_type,
        item_id=item.item_id,
        topic_id=item.topic_id,
        skill_id=item.skill_id,
        rating=rating,
        remembered=t.remembered,
        confidence=confidence,
        duration_seconds=duration_seconds,
        stage_before=item.stage,
        stage_after=t.stage,
        interval_days=t.interval_days,
        next_due=today + timedelta(days=t.interval_days),
        note=note,
    )
    item.stage = t.stage
    item.interval_days = t.interval_days
    item.ease = t.ease
    item.due_date = log.next_due
    item.review_count += 1
    item.last_reviewed_at = now()
    if t.lapse:
        item.lapses += 1
    item.success_streak = item.success_streak + 1 if rating in {"easy", "need_practice"} else 0
    db.add(log)

    _, _, label = _resolve_links(db, item.item_type, item.item_id)
    history.record(
        db, "review", item.item_type, item.item_id,
        f"Revised {label} — {rating.replace('_', ' ')}; next in {t.interval_days}d",
        skill_id=item.skill_id, topic_id=item.topic_id,
        details={"rating": rating, "stage": t.stage, "next_due": log.next_due.isoformat()},
    )
    if item.item_type == "topic":
        topic = db.get(Topic, item.item_id)
        if topic:
            if confidence is not None:
                topic.confidence = confidence
            _sync_topic_status(db, topic, rating, t.stage)
    elif item.item_type == "interview":
        q = db.get(InterviewQuestion, item.item_id)
        if q:
            q.practice_count += 1
            q.last_practised_at = now()
            if confidence is not None:
                q.confidence = confidence
            q.status = "confident" if rating == "easy" and t.stage >= 3 else "practising"
    return log
