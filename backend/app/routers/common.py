"""Small helpers shared by routers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Exercise, Project, RoadmapItem, Skill, Topic

T = TypeVar("T")

_FK_MODELS = {"skill_id": Skill, "topic_id": Topic, "project_id": Project, "exercise_id": Exercise,
              "roadmap_item_id": RoadmapItem}


def get_or_404(db: Session, model: type[T], obj_id: int, label: str | None = None) -> T:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label or model.__name__} {obj_id} not found")
    return obj


def check_refs(db: Session, data: dict[str, Any]) -> None:
    """422 if any *_id reference in `data` points at a missing row."""
    for key, model in _FK_MODELS.items():
        value = data.get(key)
        if value is not None and db.get(model, value) is None:
            raise HTTPException(status_code=422, detail=f"{key}={value} does not exist")


def iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value else None


def columns(obj: Any, *names: str) -> dict[str, Any]:
    out = {}
    for n in names:
        v = getattr(obj, n)
        out[n] = iso(v) if isinstance(v, (date, datetime)) else v
    return out
