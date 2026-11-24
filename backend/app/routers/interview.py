from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db, now
from ..models import InterviewQuestion, Project, ReviewItem, Skill, Topic
from ..schemas import InterviewIn, InterviewUpdate, PracticeIn
from .common import check_refs, columns, get_or_404

router = APIRouter(tags=["interview"])

FIELDS = ("id", "slug", "category", "difficulty", "question", "answer_md", "my_answer_md", "topic_id", "skill_id",
          "project_id", "tags", "status", "practice_count", "confidence", "last_practised_at", "source")


def _out(q: InterviewQuestion, names: dict, scheduled: set[int]) -> dict:
    return {**columns(q, *FIELDS), "skill": names["skill"].get(q.skill_id), "topic": names["topic"].get(q.topic_id),
            "project": names["project"].get(q.project_id), "in_revision": q.id in scheduled}


def _names(db: Session) -> dict:
    return {"skill": dict(db.execute(select(Skill.id, Skill.name)).all()),
            "topic": dict(db.execute(select(Topic.id, Topic.title)).all()),
            "project": dict(db.execute(select(Project.id, Project.name)).all())}


def _scheduled(db: Session) -> set[int]:
    return set(db.scalars(select(ReviewItem.item_id).where(ReviewItem.item_type == "interview", ReviewItem.is_active.is_(True))))


@router.get("/interview")
def list_questions(category: str | None = None, difficulty: str | None = None, status: str | None = None,
                   skill_id: int | None = None, project_id: int | None = None, q: str | None = None,
                   db: Session = Depends(get_db)):
    stmt = select(InterviewQuestion)
    if category:
        stmt = stmt.where(InterviewQuestion.category == category)
    if difficulty:
        stmt = stmt.where(InterviewQuestion.difficulty == difficulty)
    if status:
        stmt = stmt.where(InterviewQuestion.status == status)
    if skill_id:
        stmt = stmt.where(InterviewQuestion.skill_id == skill_id)
    if project_id:
        stmt = stmt.where(InterviewQuestion.project_id == project_id)
    if q:
        stmt = stmt.where(or_(InterviewQuestion.question.ilike(f"%{q}%"), InterviewQuestion.answer_md.ilike(f"%{q}%")))
    order = {"easy": 0, "medium": 1, "hard": 2}
    rows = sorted(db.scalars(stmt), key=lambda r: (r.category, order.get(r.difficulty, 3), r.id))
    names, sched = _names(db), _scheduled(db)
    return [_out(r, names, sched) for r in rows]


@router.get("/interview/categories")
def categories(db: Session = Depends(get_db)):
    counts: dict[str, int] = {}
    for (c,) in db.execute(select(InterviewQuestion.category)):
        counts[c] = counts.get(c, 0) + 1
    return [{"category": k, "count": v} for k, v in sorted(counts.items())]


@router.post("/interview", status_code=201)
def create_question(body: InterviewIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    check_refs(db, data)
    q = InterviewQuestion(source="user", **data)
    db.add(q)
    db.commit()
    return _out(q, _names(db), set())


@router.patch("/interview/{qid}")
def update_question(qid: int, body: InterviewUpdate, db: Session = Depends(get_db)):
    q = get_or_404(db, InterviewQuestion, qid, "Question")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    history.record_changes(db, q, data, entity_type="interview", label=q.question[:60], tracked=("status",),
                           skill_id=q.skill_id, topic_id=q.topic_id)
    db.commit()
    return _out(q, _names(db), _scheduled(db))


@router.post("/interview/{qid}/practice")
def practice(qid: int, body: PracticeIn, db: Session = Depends(get_db)):
    """Record a practice run of a question (without the spaced-repetition schedule)."""
    q = get_or_404(db, InterviewQuestion, qid, "Question")
    q.practice_count += 1
    q.confidence = body.confidence
    q.last_practised_at = now()
    if body.my_answer_md is not None:
        q.my_answer_md = body.my_answer_md
    old = q.status
    q.status = "confident" if body.confidence >= 4 else "practising"
    history.record(db, "interview_practice", "interview", q.id,
                   f"Practised interview question (confidence {body.confidence}/5): {q.question[:80]}",
                   skill_id=q.skill_id, topic_id=q.topic_id, field="status" if old != q.status else None,
                   old_value=old if old != q.status else None, new_value=q.status if old != q.status else None)
    db.commit()
    return _out(q, _names(db), _scheduled(db))


@router.delete("/interview/{qid}", status_code=204)
def delete_question(qid: int, db: Session = Depends(get_db)):
    q = get_or_404(db, InterviewQuestion, qid, "Question")
    db.query(ReviewItem).filter(ReviewItem.item_type == "interview", ReviewItem.item_id == q.id).delete()
    db.delete(q)
    db.commit()


@router.get("/interview/{qid}")
def get_question(qid: int, db: Session = Depends(get_db)):
    return _out(get_or_404(db, InterviewQuestion, qid, "Question"), _names(db), _scheduled(db))
