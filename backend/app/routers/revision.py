"""Revision queue, rating, and recall statistics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Flashcard, InterviewQuestion, ReviewItem, ReviewLog, Skill, Topic
from ..schemas import RateIn, ScheduleIn
from ..services import revision
from .common import get_or_404, iso

router = APIRouter(tags=["revision"])


def _content(db: Session, r: ReviewItem, skills: dict[int, str]) -> dict:
    base = {"id": r.id, "item_type": r.item_type, "item_id": r.item_id, "topic_id": r.topic_id,
            "skill": skills.get(r.skill_id), "due_date": iso(r.due_date), "stage": r.stage,
            "interval_days": r.interval_days, "review_count": r.review_count, "lapses": r.lapses,
            "last_reviewed_at": iso(r.last_reviewed_at),
            "preview": {rating: revision.next_state(r.stage, rating).interval_days
                        for rating in ("easy", "need_practice", "difficult", "forgot")}}
    if r.item_type == "topic":
        t = db.get(Topic, r.item_id)
        if t:
            base.update(title=t.title, prompt=f"Explain **{t.title}** in your own words. What is it, when do you use it, "
                        "and what's a common mistake?", answer_md=t.summary, detail_md=t.code_md[:4000],
                        status=t.status)
    elif r.item_type == "flashcard":
        c = db.get(Flashcard, r.item_id)
        if c:
            topic = db.get(Topic, c.topic_id) if c.topic_id else None
            base.update(title=c.front, prompt=c.front, answer_md=c.back, topic=topic.title if topic else None)
    elif r.item_type == "interview":
        q = db.get(InterviewQuestion, r.item_id)
        if q:
            base.update(title=q.question, prompt=q.question, answer_md=q.my_answer_md or q.answer_md)
    elif r.item_type == "question":
        from ..models import Question

        q = db.get(Question, r.item_id)
        if q:
            topic = db.get(Topic, q.topic_id) if q.topic_id else None
            base.update(title=q.prompt[:140], prompt=q.prompt, question=q, topic=topic.title if topic else None,
                        answer_md=q.answer_md or q.explanation)
    return base


@router.get("/revision/queue")
def queue(skill_id: int | None = None, item_type: str | None = None, days_ahead: int = 7, db: Session = Depends(get_db)):
    today = date.today()
    stmt = select(ReviewItem).where(ReviewItem.is_active.is_(True),
                                    ReviewItem.due_date <= today + timedelta(days=days_ahead))
    if skill_id:
        stmt = stmt.where(ReviewItem.skill_id == skill_id)
    if item_type:
        stmt = stmt.where(ReviewItem.item_type == item_type)
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    items = [_content(db, r, skills) for r in db.scalars(stmt.order_by(ReviewItem.due_date, ReviewItem.stage))]
    items = [i for i in items if "title" in i]  # skip orphans whose content was deleted
    return {
        "today": today.isoformat(),
        "overdue": [i for i in items if i["due_date"] < today.isoformat()],
        "due_today": [i for i in items if i["due_date"] == today.isoformat()],
        "upcoming": [i for i in items if i["due_date"] > today.isoformat()],
        "total_active": len(db.scalars(select(ReviewItem.id).where(ReviewItem.is_active.is_(True))).all()),
    }


@router.post("/revision/schedule", status_code=201)
def schedule(body: ScheduleIn, db: Session = Depends(get_db)):
    try:
        item = revision.schedule(db, body.item_type, body.item_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return {"id": item.id, "due_date": iso(item.due_date), "is_active": item.is_active}


@router.post("/revision/schedule/bulk", status_code=201)
def schedule_bulk(topic_id: int | None = None, skill_id: int | None = None, db: Session = Depends(get_db)):
    """Schedule all flashcards of a topic or skill at once."""
    if not topic_id and not skill_id:
        raise HTTPException(422, "Give topic_id or skill_id")
    stmt = select(Flashcard.id)
    stmt = stmt.where(Flashcard.topic_id == topic_id) if topic_id else stmt.where(Flashcard.skill_id == skill_id)
    ids = list(db.scalars(stmt))
    for cid in ids:
        revision.schedule(db, "flashcard", cid)
    db.commit()
    return {"scheduled": len(ids)}


@router.delete("/revision/items/{item_id}", status_code=204)
def unschedule(item_id: int, db: Session = Depends(get_db)):
    """Pause an item (history is kept; scheduling it again resumes at the same stage)."""
    item = get_or_404(db, ReviewItem, item_id, "Review item")
    item.is_active = False
    db.commit()


@router.post("/revision/items/{item_id}/rate")
def rate(item_id: int, body: RateIn, db: Session = Depends(get_db)):
    item = get_or_404(db, ReviewItem, item_id, "Review item")
    if body.client_ref and db.scalar(select(ReviewLog.id).where(ReviewLog.client_ref == body.client_ref)):
        raise HTTPException(409, "This answer was already recorded.")
    log = revision.apply_rating(db, item, body.rating, confidence=body.confidence, duration_seconds=body.duration_seconds,
                                note=body.note, client_ref=body.client_ref)
    db.commit()
    return {"next_due": iso(log.next_due), "interval_days": log.interval_days, "stage": log.stage_after,
            "remembered": log.remembered}


@router.get("/revision/stats")
def revision_stats(db: Session = Depends(get_db)):
    logs = list(db.scalars(select(ReviewLog)))
    by_item: dict[tuple[str, int], list[ReviewLog]] = defaultdict(list)
    for lg in logs:
        by_item[(lg.item_type, lg.item_id)].append(lg)

    def label(item_type: str, item_id: int) -> str | None:
        from ..models import Question

        obj = {"topic": Topic, "flashcard": Flashcard, "interview": InterviewQuestion, "question": Question}[item_type]
        row = db.get(obj, item_id)
        if not row:
            return None
        return (getattr(row, "title", None) or getattr(row, "front", None) or getattr(row, "prompt", None)
                or getattr(row, "question", ""))

    forgotten, consistent = [], []
    for (itype, iid), rows in by_item.items():
        rows.sort(key=lambda r: r.reviewed_at)
        misses = sum(r.rating in {"forgot", "difficult"} for r in rows)
        name = label(itype, iid)
        if not name:
            continue
        entry = {"item_type": itype, "item_id": iid, "title": name[:140], "reviews": len(rows), "misses": misses,
                 "topic_id": rows[-1].topic_id}
        if misses >= 2 or (misses and misses / len(rows) >= 0.5):
            forgotten.append(entry)
        recent = rows[-3:]
        if len(rows) >= 3 and all(r.remembered for r in recent):
            consistent.append(entry)
    forgotten.sort(key=lambda e: (-e["misses"], -e["reviews"]))
    consistent.sort(key=lambda e: -e["reviews"])
    today = date.today()
    active = list(db.scalars(select(ReviewItem).where(ReviewItem.is_active.is_(True))))
    return {
        "total_reviews": len(logs),
        "remembered": sum(lg.remembered for lg in logs),
        "success_rate": round(100 * sum(lg.remembered for lg in logs) / len(logs), 1) if logs else None,
        "by_rating": dict(Counter(lg.rating for lg in logs)),
        "frequently_forgotten": forgotten[:15],
        "consistently_remembered": consistent[:15],
        "due_today": sum(1 for r in active if r.due_date == today),
        "overdue": sum(1 for r in active if r.due_date < today),
        "upcoming_7d": sum(1 for r in active if today < r.due_date <= today + timedelta(days=7)),
        "by_stage": dict(Counter(r.stage for r in active)),
        "ladder": list(revision.LADDER),
    }


@router.get("/revision/logs")
def revision_logs(limit: int = 100, topic_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(ReviewLog).order_by(ReviewLog.reviewed_at.desc()).limit(min(limit, 1000))
    if topic_id:
        stmt = stmt.where(ReviewLog.topic_id == topic_id)
    return [{"id": r.id, "at": iso(r.reviewed_at), "item_type": r.item_type, "item_id": r.item_id, "rating": r.rating,
             "remembered": r.remembered, "confidence": r.confidence, "next_due": iso(r.next_due),
             "interval_days": r.interval_days} for r in db.scalars(stmt)]
