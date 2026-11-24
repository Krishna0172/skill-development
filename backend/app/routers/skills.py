from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db
from ..models import (
    Exercise,
    HistoryEvent,
    InterviewQuestion,
    LearningSession,
    Project,
    RoadmapItem,
    Skill,
    Topic,
    project_skills,
)
from ..schemas import SkillCreate, SkillUpdate
from ..services import stats
from .common import columns, get_or_404, iso

router = APIRouter(tags=["skills"])

SKILL_FIELDS = (
    "id", "slug", "name", "category", "description", "classification", "evidence_notes", "track", "status",
    "proficiency", "target_proficiency", "confidence", "next_action", "practical_experience", "notes", "is_archived", "source",
    "created_at", "updated_at",
)
TRACKED = ("proficiency", "target_proficiency", "status", "classification", "confidence")


def _skill_out(s: Skill, ov: dict | None) -> dict:
    ov = ov or {}
    return {
        **columns(s, *SKILL_FIELDS),
        "stats": {
            "topics_total": ov.get("topics_total", 0),
            "topics_done": ov.get("topics_done", 0),
            "topics_attention": ov.get("topics_attention", 0),
            "topics_revised": ov.get("topics_revised", 0),
            "exercises_total": ov.get("exercises_total", 0),
            "exercises_solved": ov.get("exercises_solved", 0),
            "hours": round(ov.get("seconds", 0) / 3600, 2),
            "due_reviews": ov.get("due_reviews", 0),
            "last_studied_at": iso(ov.get("last_studied_at")),
            "last_revised_at": iso(ov.get("last_revised_at")),
        },
    }


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70] or "skill"


@router.get("/skills")
def list_skills(
    category: str | None = None,
    status: str | None = None,
    track: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    stmt = select(Skill).order_by(Skill.category, Skill.name)
    if not include_archived:
        stmt = stmt.where(Skill.is_archived.is_(False))
    if category:
        stmt = stmt.where(Skill.category == category)
    if status:
        stmt = stmt.where(Skill.status == status)
    if track:
        stmt = stmt.where(Skill.track == track)
    if q:
        stmt = stmt.where(or_(Skill.name.ilike(f"%{q}%"), Skill.description.ilike(f"%{q}%")))
    ov = stats.skill_overview(db)
    return [_skill_out(s, ov.get(s.id)) for s in db.scalars(stmt)]


@router.get("/skills/categories")
def categories(db: Session = Depends(get_db)):
    return sorted({c for (c,) in db.execute(select(Skill.category).distinct())})


@router.post("/skills", status_code=201)
def create_skill(body: SkillCreate, db: Session = Depends(get_db)):
    base = _slugify(body.name)
    slug, i = base, 2
    while db.scalar(select(Skill.id).where(Skill.slug == slug)):
        slug, i = f"{base}-{i}", i + 1
    skill = Skill(slug=slug, source="user", **body.model_dump())
    db.add(skill)
    db.flush()
    history.record(db, "created", "skill", skill.id, f"Added skill {skill.name}", skill_id=skill.id)
    if skill.proficiency is not None:
        history.record(db, "proficiency_change", "skill", skill.id, f"{skill.name}: proficiency set to {skill.proficiency}",
                       skill_id=skill.id, field="proficiency", old_value=None, new_value=skill.proficiency)
    db.commit()
    return _skill_out(skill, None)


@router.get("/skills/{skill_id}")
def get_skill(skill_id: int, db: Session = Depends(get_db)):
    s = get_or_404(db, Skill, skill_id, "Skill")
    detail = stats.skill_detail_stats(db, s)
    topics = list(db.scalars(select(Topic).where(
        or_(Topic.skill_id == s.id, Topic.extra_skills.any(Skill.id == s.id))).order_by(Topic.title)))
    ex_status = stats._exercise_status(db)
    exercises = list(db.scalars(select(Exercise).where(Exercise.skill_id == s.id).order_by(Exercise.difficulty, Exercise.title)))
    projects = list(db.scalars(select(Project).join(project_skills).where(project_skills.c.skill_id == s.id)))
    roadmap = list(db.scalars(select(RoadmapItem).where(RoadmapItem.skill_id == s.id).order_by(RoadmapItem.sort_order)))
    events = list(db.scalars(select(HistoryEvent).where(HistoryEvent.skill_id == s.id)
                             .order_by(HistoryEvent.occurred_at.desc()).limit(50)))
    interview_count = len(db.scalars(select(InterviewQuestion.id).where(InterviewQuestion.skill_id == s.id)).all())
    return {
        **_skill_out(s, detail),
        "detail": {k: detail[k] for k in ("time_by_month", "proficiency_history", "reviews_total", "reviews_remembered",
                                          "attempts_total", "attempts_solved", "quiz_avg_pct")},
        "topics": [{"id": t.id, "title": t.title, "status": t.status, "difficulty": t.difficulty,
                    "confidence": t.confidence, "primary": t.skill_id == s.id} for t in topics],
        "exercises": [{"id": e.id, "title": e.title, "difficulty": e.difficulty, "category": e.category,
                       "status": ex_status.get(e.id, "pending")} for e in exercises],
        "projects": [{"id": p.id, "name": p.name, "kind": p.kind, "status": p.status} for p in projects],
        "roadmap": [{"id": r.id, "title": r.title, "status": r.status, "track": r.track,
                     "steps_done": sum(st.done for st in r.steps), "steps_total": len(r.steps)} for r in roadmap],
        "history": [{"at": iso(e.occurred_at), "type": e.event_type, "summary": e.summary} for e in events],
        "interview_count": interview_count,
    }


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: int, body: SkillUpdate, db: Session = Depends(get_db)):
    s = get_or_404(db, Skill, skill_id, "Skill")
    data = body.model_dump(exclude_unset=True)
    note = data.pop("change_note", "")
    history.record_changes(db, s, data, entity_type="skill", label=s.name, tracked=TRACKED, skill_id=s.id, note=note)
    db.commit()
    return _skill_out(s, stats.skill_overview(db).get(s.id))


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: int, db: Session = Depends(get_db)):
    s = get_or_404(db, Skill, skill_id, "Skill")
    if db.scalar(select(Topic.id).where(Topic.skill_id == s.id)) or db.scalar(
        select(LearningSession.id).where(LearningSession.skill_id == s.id)
    ):
        raise HTTPException(409, "This skill has linked topics or study sessions. Archive it instead to keep its history.")
    history.record(db, "deleted", "skill", s.id, f"Deleted skill {s.name}")
    db.delete(s)
    db.commit()
