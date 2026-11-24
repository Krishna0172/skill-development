from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db, now
from ..models import RoadmapItem, RoadmapStep, Skill, Topic
from ..schemas import RoadmapItemIn, RoadmapItemUpdate, RoadmapStepIn, RoadmapStepUpdate
from ..services import stats
from .common import check_refs, columns, get_or_404
from .knowledge import _unique_slug

router = APIRouter(tags=["roadmap"])

TRACK_ORDER = ["to_learn", "strengthen", "rarely_used", "advanced", "industry", "projects"]
FIELDS = ("id", "slug", "title", "skill_id", "track", "level", "sort_order", "why_md", "prerequisites_md",
          "exercises_md", "mini_projects_md", "outcome_md", "status", "started_at", "completed_at", "source")


def _item_out(item: RoadmapItem, skills: dict, topics: dict) -> dict:
    done = sum(s.done for s in item.steps)
    return {
        **columns(item, *FIELDS),
        "skill": skills.get(item.skill_id),
        "steps": [{**columns(s, "id", "title", "topic_id", "sort_order", "done", "done_at"),
                   "topic": topics.get(s.topic_id)} for s in item.steps],
        "steps_done": done, "steps_total": len(item.steps),
        "percent": round(100 * done / len(item.steps), 1) if item.steps else 0.0,
    }


def _lookups(db: Session) -> tuple[dict, dict]:
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    topics = {t.id: {"title": t.title, "status": t.status} for t in db.scalars(select(Topic))}
    return skills, topics


@router.get("/roadmap")
def get_roadmap(db: Session = Depends(get_db)):
    skills, topics = _lookups(db)
    items = list(db.scalars(select(RoadmapItem).order_by(RoadmapItem.sort_order, RoadmapItem.id)))
    return {"progress": stats.roadmap_progress(db), "track_order": TRACK_ORDER,
            "items": [_item_out(i, skills, topics) for i in items]}


def _sync_item_status(db: Session, item: RoadmapItem) -> None:
    """Keep item status consistent with its steps (unless skipped)."""
    if item.status == "skipped" or not item.steps:
        return
    done = sum(s.done for s in item.steps)
    new = "completed" if done == len(item.steps) else ("in_progress" if done else item.status)
    if new == "not_started":
        return
    if new != item.status:
        history.record(db, "status_change", "roadmap", item.id, f"Roadmap: {item.title} → {new.replace('_', ' ')}",
                       skill_id=item.skill_id, field="status", old_value=item.status, new_value=new)
        item.status = new
        item.started_at = item.started_at or now()
        item.completed_at = now() if new == "completed" else None


@router.post("/roadmap", status_code=201)
def create_item(body: RoadmapItemIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    check_refs(db, data)
    steps = data.pop("steps")
    max_order = max((i.sort_order for i in db.scalars(select(RoadmapItem))), default=0)
    item = RoadmapItem(slug=_unique_slug(db, RoadmapItem, body.title), source="user", sort_order=max_order + 1, **data)
    for i, title in enumerate(steps):
        item.steps.append(RoadmapStep(title=title, sort_order=i))
    db.add(item)
    db.commit()
    return _item_out(item, *_lookups(db))


@router.patch("/roadmap/{item_id}")
def update_item(item_id: int, body: RoadmapItemUpdate, db: Session = Depends(get_db)):
    item = get_or_404(db, RoadmapItem, item_id, "Roadmap item")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    if data.get("status") and data["status"] != item.status:
        if data["status"] == "in_progress":
            item.started_at = item.started_at or now()
        item.completed_at = now() if data["status"] == "completed" else None
    history.record_changes(db, item, data, entity_type="roadmap", label=item.title, tracked=("status",),
                           skill_id=item.skill_id)
    db.commit()
    return _item_out(item, *_lookups(db))


@router.delete("/roadmap/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = get_or_404(db, RoadmapItem, item_id, "Roadmap item")
    history.record(db, "deleted", "roadmap", item.id, f"Deleted roadmap item {item.title}")
    db.delete(item)
    db.commit()


@router.post("/roadmap/{item_id}/steps", status_code=201)
def add_step(item_id: int, body: RoadmapStepIn, db: Session = Depends(get_db)):
    item = get_or_404(db, RoadmapItem, item_id, "Roadmap item")
    check_refs(db, body.model_dump())
    item.steps.append(RoadmapStep(title=body.title, topic_id=body.topic_id, sort_order=len(item.steps)))
    db.commit()
    return _item_out(item, *_lookups(db))


@router.patch("/roadmap-steps/{step_id}")
def update_step(step_id: int, body: RoadmapStepUpdate, db: Session = Depends(get_db)):
    step = get_or_404(db, RoadmapStep, step_id, "Roadmap step")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    item = db.get(RoadmapItem, step.item_id)
    if "done" in data and data["done"] != step.done:
        step.done_at = now() if data["done"] else None
        history.record(db, "roadmap_step_done" if data["done"] else "roadmap_step_undone", "roadmap", item.id,
                       f"Roadmap step {'done' if data['done'] else 'reopened'}: {step.title}", skill_id=item.skill_id)
    for k, v in data.items():
        setattr(step, k, v)
    db.flush()
    _sync_item_status(db, item)
    db.commit()
    return _item_out(item, *_lookups(db))


@router.delete("/roadmap-steps/{step_id}", status_code=204)
def delete_step(step_id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, RoadmapStep, step_id, "Roadmap step"))
    db.commit()
