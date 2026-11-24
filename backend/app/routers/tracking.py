"""Learning sessions (timer + manual), daily logs and the history feed."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db, now
from ..models import DailyLog, HistoryEvent, LearningSession, Project, Skill, Topic
from ..schemas import DailyLogIn, SessionCreate, SessionUpdate, TimerStartIn, TimerStopIn
from ..services import stats, timecalc
from .common import check_refs, columns, get_or_404, iso

router = APIRouter(tags=["tracking"])

FIELDS = ("id", "kind", "title", "skill_id", "topic_id", "project_id", "exercise_id", "started_at", "ended_at",
          "source", "notes", "problems")


def _out(s: LearningSession, names: dict | None = None) -> dict:
    end = s.ended_at or now()
    out = {**columns(s, *FIELDS), "minutes": round((end - s.started_at).total_seconds() / 60, 1),
           "running": s.ended_at is None}
    if names:
        out.update(skill=names["skill"].get(s.skill_id), topic=names["topic"].get(s.topic_id),
                   project=names["project"].get(s.project_id))
    return out


def _names(db: Session) -> dict:
    return {"skill": dict(db.execute(select(Skill.id, Skill.name)).all()),
            "topic": dict(db.execute(select(Topic.id, Topic.title)).all()),
            "project": dict(db.execute(select(Project.id, Project.name)).all())}


def _label(db: Session, s: LearningSession) -> str:
    parts = [s.title] if s.title else []
    if s.topic_id and (t := db.get(Topic, s.topic_id)):
        parts.append(t.title)
    elif s.skill_id and (sk := db.get(Skill, s.skill_id)):
        parts.append(sk.name)
    return " · ".join(parts) or s.kind


def _fill_skill(db: Session, data: dict) -> None:
    """A session on a topic counts for the topic's skill unless one is given."""
    if data.get("topic_id") and not data.get("skill_id"):
        data["skill_id"] = db.get(Topic, data["topic_id"]).skill_id


def _overlaps(db: Session, start: datetime, end: datetime, exclude_id: int | None = None) -> list[LearningSession]:
    candidates = db.scalars(select(LearningSession).where(
        LearningSession.started_at < end,
        (LearningSession.ended_at.is_(None)) | (LearningSession.ended_at > start)))
    return timecalc.find_overlaps(candidates, start, end, now(), exclude_id)


@router.get("/sessions")
def list_sessions(start: date | None = None, end: date | None = None, skill_id: int | None = None,
                  topic_id: int | None = None, kind: str | None = None, limit: int = 500, db: Session = Depends(get_db)):
    stmt = select(LearningSession).order_by(LearningSession.started_at.desc())
    if start:
        stmt = stmt.where(LearningSession.started_at >= datetime.combine(start, time.min))
    if end:
        stmt = stmt.where(LearningSession.started_at < datetime.combine(end + timedelta(days=1), time.min))
    if skill_id:
        stmt = stmt.where(LearningSession.skill_id == skill_id)
    if topic_id:
        stmt = stmt.where(LearningSession.topic_id == topic_id)
    if kind:
        stmt = stmt.where(LearningSession.kind == kind)
    names = _names(db)
    return [_out(s, names) for s in db.scalars(stmt.limit(min(limit, 5000)))]


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, db: Session = Depends(get_db)):
    data = body.model_dump()
    check_refs(db, data)
    allow = data.pop("allow_overlap")
    if body.ended_at > now() + timedelta(minutes=5):
        raise HTTPException(422, "A manual session can't end in the future.")
    if body.client_ref and db.scalar(select(LearningSession.id).where(LearningSession.client_ref == body.client_ref)):
        raise HTTPException(409, "This session was already saved.")
    overlaps = _overlaps(db, body.started_at, body.ended_at)
    if overlaps and not allow:
        raise HTTPException(409, {
            "message": "This time overlaps existing sessions. Overlapping time is only counted once in totals; "
                       "save anyway with allow_overlap=true, or adjust the times.",
            "overlaps": [_out(s) for s in overlaps],
        })
    _fill_skill(db, data)
    s = LearningSession(source="manual", **data)
    db.add(s)
    db.flush()
    history.record(db, "session", "session", s.id, f"Logged {round((s.ended_at - s.started_at).total_seconds() / 60)} min "
                   f"{s.kind}: {_label(db, s)}", skill_id=s.skill_id, topic_id=s.topic_id)
    db.commit()
    return _out(s, _names(db))


@router.patch("/sessions/{session_id}")
def update_session(session_id: int, body: SessionUpdate, db: Session = Depends(get_db)):
    s = get_or_404(db, LearningSession, session_id, "Session")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    allow = data.pop("allow_overlap", False)
    start = data.get("started_at", s.started_at)
    end = data.get("ended_at", s.ended_at)
    if end is not None:
        if end <= start:
            raise HTTPException(422, "ended_at must be after started_at")
        overlaps = _overlaps(db, start, end, exclude_id=s.id)
        if overlaps and not allow and ("started_at" in data or "ended_at" in data):
            raise HTTPException(409, {"message": "The new times overlap other sessions.",
                                      "overlaps": [_out(o) for o in overlaps]})
    if "topic_id" in data and "skill_id" not in data:
        _fill_skill(db, data)
    old = (s.started_at, s.ended_at)
    for k, v in data.items():
        setattr(s, k, v)
    if (s.started_at, s.ended_at) != old:
        history.record(db, "session_edited", "session", s.id, f"Edited session times: {_label(db, s)}",
                       skill_id=s.skill_id, topic_id=s.topic_id,
                       details={"old": [iso(old[0]), iso(old[1])], "new": [iso(s.started_at), iso(s.ended_at)]})
    db.commit()
    return _out(s, _names(db))


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    s = get_or_404(db, LearningSession, session_id, "Session")
    history.record(db, "deleted", "session", s.id, f"Deleted session: {_label(db, s)} ({iso(s.started_at)})",
                   skill_id=s.skill_id, topic_id=s.topic_id)
    db.delete(s)
    db.commit()


# --------------------------------------------------------------------------- timer


@router.get("/timer")
def active_timer(db: Session = Depends(get_db)):
    s = db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None)))
    return _out(s, _names(db)) if s else None


@router.post("/timer/start", status_code=201)
def start_timer(body: TimerStartIn, db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    running = db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None)))
    if running:  # only one timer at a time: stopping it prevents overlapping time
        _stop(db, running)
    _fill_skill(db, data)
    s = LearningSession(source="timer", started_at=now(), **data)
    db.add(s)
    db.commit()
    return _out(s, _names(db))


def _stop(db: Session, s: LearningSession) -> None:
    s.ended_at = now()
    if s.ended_at <= s.started_at:
        s.ended_at = s.started_at + timedelta(seconds=1)
    mins = round((s.ended_at - s.started_at).total_seconds() / 60)
    history.record(db, "session", "session", s.id, f"Studied {mins} min ({s.kind}): {_label(db, s)}",
                   skill_id=s.skill_id, topic_id=s.topic_id)


@router.post("/timer/stop")
def stop_timer(body: TimerStopIn | None = None, db: Session = Depends(get_db)):
    s = db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None)))
    if not s:
        raise HTTPException(404, "No timer is running.")
    if body:
        if body.notes is not None:
            s.notes = body.notes
        if body.problems is not None:
            s.problems = body.problems
    _stop(db, s)
    db.commit()
    return _out(s, _names(db))


@router.post("/timer/discard", status_code=204)
def discard_timer(db: Session = Depends(get_db)):
    s = db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None)))
    if s:
        db.delete(s)
        db.commit()


# --------------------------------------------------------------------------- daily log & activity


def _log_out(log: DailyLog) -> dict:
    return columns(log, "id", "log_date", "studied_md", "accomplished_md", "problems_md", "tomorrow_md", "energy", "updated_at")


@router.get("/daily-logs")
def list_logs(start: date | None = None, end: date | None = None, db: Session = Depends(get_db)):
    stmt = select(DailyLog).order_by(DailyLog.log_date.desc())
    if start:
        stmt = stmt.where(DailyLog.log_date >= start)
    if end:
        stmt = stmt.where(DailyLog.log_date <= end)
    return [_log_out(lg) for lg in db.scalars(stmt.limit(400))]


@router.put("/daily-logs/{log_date}")
def upsert_log(log_date: date, body: DailyLogIn, db: Session = Depends(get_db)):
    if log_date > date.today():
        raise HTTPException(422, "Can't write a log for a future date.")
    log = db.scalar(select(DailyLog).where(DailyLog.log_date == log_date))
    created = log is None
    if created:
        log = DailyLog(log_date=log_date)
        db.add(log)
    for k, v in body.model_dump().items():
        setattr(log, k, v)
    db.flush()
    if created:
        history.record(db, "daily_log", "daily_log", log.id, f"Wrote end-of-day log for {log_date.isoformat()}")
    db.commit()
    return _log_out(log)


@router.get("/activity/day/{day}")
def day_activity(day: date, db: Session = Depends(get_db)):
    return stats.day_summary(db, day)


@router.get("/history")
def history_feed(entity_type: str | None = None, skill_id: int | None = None, topic_id: int | None = None,
                 event_type: str | None = None, start: date | None = None, end: date | None = None,
                 limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    stmt = select(HistoryEvent).order_by(HistoryEvent.occurred_at.desc(), HistoryEvent.id.desc())
    if entity_type:
        stmt = stmt.where(HistoryEvent.entity_type == entity_type)
    if skill_id:
        stmt = stmt.where(HistoryEvent.skill_id == skill_id)
    if topic_id:
        stmt = stmt.where(HistoryEvent.topic_id == topic_id)
    if event_type:
        stmt = stmt.where(HistoryEvent.event_type == event_type)
    if start:
        stmt = stmt.where(HistoryEvent.occurred_at >= datetime.combine(start, time.min))
    if end:
        stmt = stmt.where(HistoryEvent.occurred_at < datetime.combine(end + timedelta(days=1), time.min))
    rows = db.scalars(stmt.offset(offset).limit(min(limit, 1000)))
    return [{**columns(e, "id", "occurred_at", "event_type", "entity_type", "entity_id", "skill_id", "topic_id",
                       "field", "old_value", "new_value", "summary")} for e in rows]
