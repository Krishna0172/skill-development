"""Activity tracker: year heatmap, day view, session edit/delete and the end-of-day log."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LearningSession, Topic
from ..routers import tracking as tracking_api
from ..schemas import DailyLogIn, SessionCreate, SessionUpdate
from ..services import stats
from .core import form_int, redirect, render

router = APIRouter(include_in_schema=False)

KINDS = ["learning", "practice", "revision", "project", "reading", "other"]


def _err(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return str(exc.errors()[0]["msg"])
    detail = getattr(exc, "detail", exc)
    return str(detail.get("message") if isinstance(detail, dict) else detail)[:300]


def _parse_day(day: str) -> date:
    try:
        d = date.fromisoformat(day) if day else date.today()
    except ValueError:
        d = date.today()
    return min(d, date.today())


def _at(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())


@router.get("/tracker")
def tracker_page(request: Request, day: str = "", db: Session = Depends(get_db)):
    d = _parse_day(day)
    today = date.today()
    heat = stats.analytics(db, today - timedelta(days=364), today)["heatmap"]
    summary = stats.day_summary(db, d)
    log = summary["log"] or {}
    return render(request, "tracker.html", {
        "d": d, "day": summary, "heat": heat, "log": log, "kinds": KINDS, "streak": stats.streaks(db),
        "prev": (d - timedelta(days=1)).isoformat(), "next": (d + timedelta(days=1)).isoformat() if d < today else None,
        "is_today": d == today, "year_minutes": sum(h["minutes"] for h in heat),
        "active_days": sum(1 for h in heat if h["active"] or h["minutes"]),
        "topics": db.execute(select(Topic.id, Topic.title).order_by(Topic.title)).all(),
        "heat_tip": lambda h: f"{h['date']}: {h['minutes']} min", "heat_href": lambda h: f"/tracker?day={h['date']}"})


@router.post("/tracker/sessions")
def session_create(day: str = Form(...), start: str = Form(...), end: str = Form(...), kind: str = Form("learning"),
                   topic_id: str = Form(""), title: str = Form(""), notes: str = Form(""), db: Session = Depends(get_db)):
    d = _parse_day(day)
    back = f"/tracker?day={d.isoformat()}"
    try:
        tid = form_int(topic_id)
        topic = db.get(Topic, tid) if tid else None
        body = SessionCreate(kind=kind, title=title or (topic.title if topic else kind), topic_id=tid,
                             started_at=_at(d, start), ended_at=_at(d, end), notes=notes, allow_overlap=True)
        tracking_api.create_session(body, db)
    except (ValidationError, HTTPException, ValueError) as exc:
        return redirect(back, err=_err(exc))
    return redirect(back, "Session added.")


@router.post("/tracker/sessions/{session_id}")
def session_update(session_id: int, start: str = Form(...), end: str = Form(""), kind: str = Form("learning"),
                   title: str = Form(""), notes: str = Form(""), db: Session = Depends(get_db)):
    s = db.get(LearningSession, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    d = s.started_at.date()
    back = f"/tracker?day={d.isoformat()}"
    try:
        data = {"started_at": _at(d, start), "kind": kind, "title": title, "notes": notes, "allow_overlap": True}
        if end:
            ended = _at(d, end)
            data["ended_at"] = ended if ended > data["started_at"] else ended + timedelta(days=1)
        tracking_api.update_session(session_id, SessionUpdate(**data), db)
    except (ValidationError, HTTPException, ValueError) as exc:
        return redirect(back, err=_err(exc))
    return redirect(back, "Session updated.")


@router.post("/tracker/sessions/{session_id}/delete")
def session_delete(session_id: int, db: Session = Depends(get_db)):
    s = db.get(LearningSession, session_id)
    if s is None:
        return redirect("/tracker", err="Session not found.")
    back = f"/tracker?day={s.started_at.date().isoformat()}"
    tracking_api.delete_session(session_id, db)
    return redirect(back, "Session deleted.")


@router.post("/tracker/log")
def log_save(day: str = Form(...), studied_md: str = Form(""), accomplished_md: str = Form(""), problems_md: str = Form(""),
             tomorrow_md: str = Form(""), energy: str = Form(""), db: Session = Depends(get_db)):
    d = _parse_day(day)
    try:
        tracking_api.upsert_log(d, DailyLogIn(studied_md=studied_md, accomplished_md=accomplished_md,
                                              problems_md=problems_md, tomorrow_md=tomorrow_md,
                                              energy=form_int(energy)), db)
    except (ValidationError, HTTPException) as exc:
        return redirect(f"/tracker?day={d.isoformat()}#log", err=_err(exc))
    return redirect(f"/tracker?day={d.isoformat()}#log", "Daily log saved.")
