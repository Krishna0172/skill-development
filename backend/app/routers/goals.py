from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db, now
from ..models import Goal, Skill
from ..schemas import GoalCreate, GoalUpdate
from ..services import goals as goal_service
from .common import check_refs, columns, get_or_404

router = APIRouter(tags=["goals"])

FIELDS = ("id", "title", "description", "skill_id", "target_date", "priority", "status", "metric", "metric_target",
          "metric_filter", "count_from", "manual_progress", "completed_at", "created_at", "updated_at")


def _out(db: Session, g: Goal, skills: dict | None = None) -> dict:
    skills = skills if skills is not None else dict(db.execute(select(Skill.id, Skill.name)).all())
    return {**columns(g, *FIELDS), "skill": skills.get(g.skill_id), "progress": goal_service.compute(db, g)}


@router.get("/goals")
def list_goals(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Goal)
    if status:
        stmt = stmt.where(Goal.status.in_(status.split(",")))
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    out = [_out(db, g, skills) for g in db.scalars(stmt)]
    prio = {"high": 0, "medium": 1, "low": 2}
    closed = {"completed", "abandoned"}
    out.sort(key=lambda g: (g["status"] in closed, g["target_date"] or "9999", prio.get(g["priority"], 3)))
    return out


@router.post("/goals", status_code=201)
def create_goal(body: GoalCreate, db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    check_refs(db, {**data, **(data.get("metric_filter") or {})})
    g = Goal(**data)
    db.add(g)
    db.flush()
    if g.count_from is None:
        g.count_from = g.created_at.date()
    history.record(db, "created", "goal", g.id, f"New goal: {g.title}", skill_id=g.skill_id)
    db.commit()
    return _out(db, g)


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalUpdate, db: Session = Depends(get_db)):
    g = get_or_404(db, Goal, goal_id, "Goal")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, {**data, **(data.get("metric_filter") or {})})
    if "status" in data and data["status"] != g.status:
        g.completed_at = now() if data["status"] == "completed" else None
    history.record_changes(db, g, data, entity_type="goal", label=g.title, tracked=("status", "target_date", "manual_progress"),
                           skill_id=g.skill_id)
    db.commit()
    return _out(db, g)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: int, db: Session = Depends(get_db)):
    g = get_or_404(db, Goal, goal_id, "Goal")
    history.record(db, "deleted", "goal", g.id, f"Deleted goal {g.title}")
    db.delete(g)
    db.commit()
