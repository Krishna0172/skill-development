"""Append-only history journal helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import HistoryEvent


def _short(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= 200 else text[:197] + "..."


def record(
    db: Session,
    event_type: str,
    entity_type: str,
    entity_id: int | None,
    summary: str,
    *,
    skill_id: int | None = None,
    topic_id: int | None = None,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    details: dict | None = None,
) -> HistoryEvent:
    """Add a history event to the session (caller commits)."""
    event = HistoryEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        skill_id=skill_id,
        topic_id=topic_id,
        field=field,
        old_value=_short(old_value),
        new_value=_short(new_value),
        summary=summary[:400],
        details=details or {},
    )
    db.add(event)
    return event


def record_changes(
    db: Session,
    obj: Any,
    changes: dict[str, Any],
    *,
    entity_type: str,
    label: str,
    tracked: tuple[str, ...],
    skill_id: int | None = None,
    topic_id: int | None = None,
    note: str = "",
) -> list[str]:
    """Apply `changes` to `obj`; log a 'change' event for every tracked field whose value
    actually changed. Returns the list of tracked fields that changed."""
    changed: list[str] = []
    for key, new in changes.items():
        old = getattr(obj, key)
        if old == new:
            continue
        setattr(obj, key, new)
        if key in tracked:
            changed.append(key)
            record(
                db,
                f"{key}_change",
                entity_type,
                obj.id,
                f"{label}: {key.replace('_', ' ')} {_fmt(old)} → {_fmt(new)}" + (f" ({note})" if note else ""),
                skill_id=skill_id,
                topic_id=topic_id,
                field=key,
                old_value=old,
                new_value=new,
            )
    return changed


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("_", " ")
