"""Knowledge base: topics, flashcards, questions and assessments."""

from __future__ import annotations

import random
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db, now
from ..models import (
    Exercise,
    Flashcard,
    HistoryEvent,
    InterviewQuestion,
    LearningSession,
    Project,
    AssessmentAttempt,
    Question,
    ReviewItem,
    Skill,
    Topic,
    project_topics,
)
from ..schemas import AssessmentIn, FlashcardIn, FlashcardUpdate, QuestionIn, TopicCreate, TopicUpdate
from ..services import assessment, revision, stats, timecalc
from ..services import topics as topic_service
from .common import check_refs, columns, get_or_404, iso

router = APIRouter(tags=["knowledge"])

TOPIC_BRIEF = ("id", "slug", "title", "skill_id", "category", "difficulty", "tags", "summary", "status",
               "confidence", "source", "started_at", "completed_at", "updated_at")
TOPIC_FULL = TOPIC_BRIEF + ("details_md", "code_md", "use_cases_md", "mistakes_md", "best_practices_md",
                            "my_context_md", "why_md", "revision_notes_md", "exercises_md", "notes_md", "track", "created_at")


def _review_map(db: Session, item_type: str) -> dict[int, ReviewItem]:
    return {r.item_id: r for r in db.scalars(select(ReviewItem).where(ReviewItem.item_type == item_type))}


def _review_out(r: ReviewItem | None) -> dict | None:
    if not r:
        return None
    return {"id": r.id, "due_date": iso(r.due_date), "stage": r.stage, "interval_days": r.interval_days,
            "review_count": r.review_count, "lapses": r.lapses, "is_active": r.is_active,
            "last_reviewed_at": iso(r.last_reviewed_at)}


# --------------------------------------------------------------------------- topics


@router.get("/topics")
def list_topics(
    q: str | None = None,
    skill_id: int | None = None,
    category: str | None = None,
    status: str | None = None,
    difficulty: str | None = None,
    tag: str | None = None,
    sort: str = "title",
    db: Session = Depends(get_db),
):
    stmt = select(Topic)
    if q:
        for term in q.split():
            like = f"%{term}%"
            stmt = stmt.where(or_(Topic.title.ilike(like), Topic.summary.ilike(like), Topic.details_md.ilike(like),
                                  Topic.code_md.ilike(like), Topic.notes_md.ilike(like)))
    if skill_id:
        stmt = stmt.where(or_(Topic.skill_id == skill_id, Topic.extra_skills.any(Skill.id == skill_id)))
    if category:
        stmt = stmt.where(Topic.category == category)
    if status:
        stmt = stmt.where(Topic.status.in_(status.split(",")))
    if difficulty:
        stmt = stmt.where(Topic.difficulty == difficulty)
    order = {"title": Topic.title, "updated": Topic.updated_at.desc(), "status": Topic.status}.get(sort, Topic.title)
    topics = list(db.scalars(stmt.order_by(order)))
    if tag:
        topics = [t for t in topics if tag in (t.tags or [])]
    reviews = _review_map(db, "topic")
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    return [{**columns(t, *TOPIC_BRIEF), "skill": skills.get(t.skill_id), "review": _review_out(reviews.get(t.id))}
            for t in topics]


@router.get("/topics/facets")
def topic_facets(db: Session = Depends(get_db)):
    cats = sorted({c for (c,) in db.execute(select(Topic.category).distinct())})
    tags: dict[str, int] = {}
    for (tl,) in db.execute(select(Topic.tags)):
        for t in tl or []:
            tags[t] = tags.get(t, 0) + 1
    return {"categories": cats, "tags": sorted(tags, key=lambda k: -tags[k])[:60]}


def _unique_slug(db: Session, model, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "item"
    slug, i = base, 2
    while db.scalar(select(model.id).where(model.slug == slug)):
        slug, i = f"{base}-{i}", i + 1
    return slug


def _apply_links(db: Session, topic: Topic, data: dict) -> None:
    if "extra_skill_ids" in data:
        ids = data.pop("extra_skill_ids") or []
        topic.extra_skills = list(db.scalars(select(Skill).where(Skill.id.in_(ids)))) if ids else []
    if "related_ids" in data:
        ids = [i for i in (data.pop("related_ids") or []) if i != topic.id]
        topic.related = list(db.scalars(select(Topic).where(Topic.id.in_(ids)))) if ids else []


@router.post("/topics", status_code=201)
def create_topic(body: TopicCreate, db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    add_to_revision = data.pop("add_to_revision", False)
    status = data.pop("status", "not_started") or "not_started"
    topic = Topic(slug=_unique_slug(db, Topic, body.title), source="user", status="not_started")
    _apply_links(db, topic, data)
    for k, v in data.items():
        setattr(topic, k, v)
    db.add(topic)
    db.flush()
    history.record(db, "created", "topic", topic.id, f"Added topic {topic.title}", skill_id=topic.skill_id, topic_id=topic.id)
    # a topic you write yourself has no questions yet, so learned/mastered stays allowed
    topic_service.set_status(db, topic, status, require_evidence=True)
    if add_to_revision:
        revision.schedule(db, "topic", topic.id)
    db.commit()
    return get_topic(topic.id, db)


@router.get("/topics/{topic_id}")
def get_topic(topic_id: int, db: Session = Depends(get_db)):
    t = get_or_404(db, Topic, topic_id, "Topic")
    review = db.scalar(select(ReviewItem).where(ReviewItem.item_type == "topic", ReviewItem.item_id == t.id))
    card_reviews = _review_map(db, "flashcard")
    # related links are stored one-way; show both directions
    back = list(db.scalars(select(Topic).where(Topic.related.any(Topic.id == t.id))))
    related = {r.id: r for r in [*t.related, *back]}
    skill = db.get(Skill, t.skill_id) if t.skill_id else None
    interviews = list(db.scalars(select(InterviewQuestion).where(InterviewQuestion.topic_id == t.id)))
    exercises = list(db.scalars(select(Exercise).where(Exercise.topics.any(Topic.id == t.id))))
    ex_status = stats._exercise_status(db)
    projects = list(db.scalars(select(Project).join(project_topics).where(project_topics.c.topic_id == t.id)))
    events = list(db.scalars(select(HistoryEvent).where(HistoryEvent.topic_id == t.id)
                             .order_by(HistoryEvent.occurred_at.desc()).limit(40)))
    attempts = list(db.scalars(select(AssessmentAttempt).where(AssessmentAttempt.topic_id == t.id)
                               .order_by(AssessmentAttempt.taken_at.desc())))
    sessions = list(db.scalars(select(LearningSession).where(LearningSession.topic_id == t.id)))
    from ..models import ReviewLog
    logs = list(db.scalars(select(ReviewLog).where(ReviewLog.item_type == "topic", ReviewLog.item_id == t.id)
                           .order_by(ReviewLog.reviewed_at.desc())))
    return {
        **columns(t, *TOPIC_FULL),
        "skill": {"id": skill.id, "name": skill.name} if skill else None,
        "extra_skills": [{"id": s.id, "name": s.name} for s in t.extra_skills],
        "related": [{"id": r.id, "title": r.title, "status": r.status} for r in related.values()],
        "flashcards": [{"id": c.id, "front": c.front, "back": c.back, "source": c.source,
                        "review": _review_out(card_reviews.get(c.id))} for c in t.flashcards],
        "question_count": len(t.questions),
        "question_types": sorted({q.type for q in t.questions}),
        "assessments": [{"id": a.id, "at": iso(a.taken_at), "score_pct": a.score_pct, "passed": a.passed} for a in attempts[:10]],
        "best_score": max((a.score_pct for a in attempts if a.mode == "assessment"), default=None),
        "interview": [{"id": q.id, "question": q.question, "difficulty": q.difficulty, "answer_md": q.answer_md,
                       "status": q.status} for q in interviews],
        "exercises": [{"id": e.id, "title": e.title, "difficulty": e.difficulty, "status": ex_status.get(e.id, "pending")}
                      for e in exercises],
        "projects": [{"id": p.id, "name": p.name} for p in projects],
        "review": _review_out(review),
        "review_logs": [{"at": iso(r.reviewed_at), "rating": r.rating, "next_due": iso(r.next_due)} for r in logs[:20]],
        "practice_attempts": sum(len(e.attempts) for e in exercises),
        "minutes_spent": round(timecalc.total_seconds(timecalc.session_interval(s, now()) for s in sessions) / 60),
        "history": [{"at": iso(e.occurred_at), "type": e.event_type, "summary": e.summary} for e in events],
    }


@router.patch("/topics/{topic_id}")
def update_topic(topic_id: int, body: TopicUpdate, db: Session = Depends(get_db)):
    t = get_or_404(db, Topic, topic_id, "Topic")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    note = data.pop("change_note", "")
    new_status = data.pop("status", None)
    _apply_links(db, t, data)
    history.record_changes(db, t, data, entity_type="topic", label=t.title, tracked=("confidence", "skill_id"),
                           skill_id=t.skill_id, topic_id=t.id, note=note)
    if new_status:
        try:
            topic_service.set_status(db, t, new_status, note, require_evidence=True)
        except topic_service.MasteryEvidenceError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
    db.commit()
    return get_topic(t.id, db)


@router.delete("/topics/{topic_id}", status_code=204)
def delete_topic(topic_id: int, db: Session = Depends(get_db)):
    t = get_or_404(db, Topic, topic_id, "Topic")
    history.record(db, "deleted", "topic", t.id, f"Deleted topic {t.title}", skill_id=t.skill_id)
    db.query(ReviewItem).filter(ReviewItem.item_type == "topic", ReviewItem.item_id == t.id).delete()
    card_ids = [c.id for c in t.flashcards]
    if card_ids:
        db.query(ReviewItem).filter(ReviewItem.item_type == "flashcard", ReviewItem.item_id.in_(card_ids)).delete()
    db.delete(t)
    db.commit()


# --------------------------------------------------------------------------- flashcards


@router.get("/flashcards")
def list_flashcards(topic_id: int | None = None, skill_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(Flashcard)
    if topic_id:
        stmt = stmt.where(Flashcard.topic_id == topic_id)
    if skill_id:
        stmt = stmt.where(Flashcard.skill_id == skill_id)
    reviews = _review_map(db, "flashcard")
    topics = dict(db.execute(select(Topic.id, Topic.title)).all())
    return [{**columns(c, "id", "topic_id", "skill_id", "front", "back", "source"), "topic": topics.get(c.topic_id),
             "review": _review_out(reviews.get(c.id))} for c in db.scalars(stmt.order_by(Flashcard.id))]


@router.post("/flashcards", status_code=201)
def create_flashcard(body: FlashcardIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    check_refs(db, data)
    add = data.pop("add_to_revision")
    if data.get("topic_id") and not data.get("skill_id"):
        data["skill_id"] = db.get(Topic, data["topic_id"]).skill_id
    card = Flashcard(source="user", **data)
    db.add(card)
    db.flush()
    if add:
        revision.schedule(db, "flashcard", card.id)
    db.commit()
    return columns(card, "id", "topic_id", "skill_id", "front", "back", "source")


@router.patch("/flashcards/{card_id}")
def update_flashcard(card_id: int, body: FlashcardUpdate, db: Session = Depends(get_db)):
    card = get_or_404(db, Flashcard, card_id, "Flashcard")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    for k, v in data.items():
        setattr(card, k, v)
    db.commit()
    return columns(card, "id", "topic_id", "skill_id", "front", "back", "source")


@router.delete("/flashcards/{card_id}", status_code=204)
def delete_flashcard(card_id: int, db: Session = Depends(get_db)):
    card = get_or_404(db, Flashcard, card_id, "Flashcard")
    db.query(ReviewItem).filter(ReviewItem.item_type == "flashcard", ReviewItem.item_id == card.id).delete()
    db.delete(card)
    db.commit()


# --------------------------------------------------------------------------- questions & assessments


def question_out(q: Question, *, reveal: bool = False) -> dict:
    """Question for display. Answers are hidden unless `reveal` (after grading / for self-graded review)."""
    out = columns(q, "id", "topic_id", "type", "prompt", "code", "options", "rubric", "position")
    if reveal:
        out.update(columns(q, "answer_index", "answers", "answer_md", "explanation"))
    return out


@router.get("/topics/{topic_id}/questions")
def topic_questions(topic_id: int, db: Session = Depends(get_db)):
    t = get_or_404(db, Topic, topic_id, "Topic")
    return [question_out(q) for q in t.questions]


@router.post("/questions", status_code=201)
def create_question(body: QuestionIn, db: Session = Depends(get_db)):
    topic = get_or_404(db, Topic, body.topic_id, "Topic")
    q = Question(skill_id=topic.skill_id, source="user", position=len(topic.questions), **body.model_dump())
    db.add(q)
    db.commit()
    return question_out(q, reveal=True)


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(question_id: int, db: Session = Depends(get_db)):
    q = get_or_404(db, Question, question_id, "Question")
    db.query(ReviewItem).filter(ReviewItem.item_type == "question", ReviewItem.item_id == q.id).delete()
    db.delete(q)
    db.commit()


@router.get("/quiz/questions")
def mixed_quiz(skill_id: int | None = None, track: str | None = None, limit: int = 10, auto_only: bool = True,
               db: Session = Depends(get_db)):
    """Random questions across topics (auto-graded types by default) for a quick quiz."""
    stmt = select(Question).join(Topic, isouter=True)
    if auto_only:
        stmt = stmt.where(Question.type.in_(["mcq", "fill_blank", "output"]))
    if skill_id:
        stmt = stmt.where(or_(Question.skill_id == skill_id, Topic.skill_id == skill_id))
    if track:
        stmt = stmt.where(Topic.track == track)
    pool = list(db.scalars(stmt))
    random.shuffle(pool)
    return [question_out(q) for q in pool[: max(1, min(limit, 50))]]


@router.post("/assessments", status_code=201)
def submit_assessment(body: AssessmentIn, db: Session = Depends(get_db)):
    if body.client_ref and db.scalar(select(AssessmentAttempt.id).where(AssessmentAttempt.client_ref == body.client_ref)):
        raise HTTPException(409, "This assessment was already submitted.")
    if body.topic_id is not None:
        get_or_404(db, Topic, body.topic_id, "Topic")
    try:
        attempt = assessment.record_attempt(
            db, [r.model_dump() for r in body.responses], topic_id=body.topic_id, mode=body.mode, role=body.role,
            duration_seconds=body.duration_seconds, client_ref=body.client_ref)
    except LookupError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return assessment_result(attempt.id, db)


@router.get("/assessments/{attempt_id}")
def assessment_result(attempt_id: int, db: Session = Depends(get_db)):
    a = get_or_404(db, AssessmentAttempt, attempt_id, "Assessment")
    qs = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_([d["question_id"] for d in a.details])))}
    topic = db.get(Topic, a.topic_id) if a.topic_id else None
    return {
        **columns(a, "id", "taken_at", "mode", "role", "topic_id", "total", "score", "score_pct", "passed", "duration_seconds"),
        "topic": {"id": topic.id, "title": topic.title, "status": topic.status} if topic else None,
        "pass_pct": assessment.PASS_PCT,
        "items": [{**d, "question": question_out(qs[d["question_id"]], reveal=True)} for d in a.details if d["question_id"] in qs],
    }
