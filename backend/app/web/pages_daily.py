"""Today, dashboard, timer, quick-add activity, theme switch and global search."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, now
from ..models import LearningSession, Topic
from ..routers import analytics as analytics_api
from ..routers import tracking as tracking_api
from ..schemas import SessionCreate, TimerStartIn
from ..services import progress, search as search_service, stats
from .core import form_int, redirect, render

router = APIRouter(include_in_schema=False)


@router.get("/")
def today(request: Request, db: Session = Depends(get_db)):
    t = analytics_api.today_plan(db)
    return render(request, "today.html", {
        "t": t, "areas": progress.area_progress(db), "targets": progress.targets_progress(db),
        "roles": progress.role_readiness(db)[:3],
    })


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    return render(request, "dashboard.html", {
        "d": stats.dashboard(db), "areas": progress.area_progress(db), "roles": progress.role_readiness(db),
        "targets": progress.targets_progress(db),
    })


@router.get("/theme")
def theme(request: Request, next: str = "/"):
    current = request.cookies.get("theme", "dark")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("theme", "light" if current == "dark" else "dark", max_age=10 * 365 * 86400, samesite="lax")
    return resp


@router.post("/timer/start")
def timer_start(request: Request, topic_id: str = Form(""), kind: str = Form("learning"), db: Session = Depends(get_db)):
    tracking_api.start_timer(TimerStartIn(kind=kind if kind in {"learning", "practice", "revision", "project", "reading", "other"} else "learning",
                                          topic_id=form_int(topic_id)), db)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@router.post("/timer/stop")
def timer_stop(request: Request, db: Session = Depends(get_db)):
    if db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None))):
        tracking_api.stop_timer(None, db)
    return redirect(_back(request), "Timer stopped — time recorded.")


def _back(request: Request, fallback: str = "/") -> str:
    ref = request.headers.get("referer") or fallback
    path = "/" + ref.split("://", 1)[-1].split("/", 1)[-1] if "://" in ref else ref
    return path.split("?")[0] or fallback


@router.post("/tracker/quick-add")
def quick_add(
    minutes: int = Form(...), kind: str = Form("learning"), day: str = Form(""), topic_id: str = Form(""),
    notes: str = Form(""), difficulty: str = Form(""), confidence: str = Form(""), problems: str = Form(""),
    next_action: str = Form(""), next: str = Form("/"), db: Session = Depends(get_db),
):
    target = next if next.startswith("/") else "/"
    try:
        d = date.fromisoformat(day) if day else date.today()
        minutes = max(1, min(int(minutes), 960))
        end = now() if d == date.today() else datetime.combine(d, time(21, 0))
        start = end - timedelta(minutes=minutes)
        if start.date() != end.date():
            start = datetime.combine(end.date(), time.min)
        tid = form_int(topic_id)
        topic = db.get(Topic, tid) if tid else None
        body = SessionCreate(
            kind=kind, title=topic.title if topic else (notes[:60] or kind), topic_id=tid,
            started_at=start, ended_at=end, notes=notes, problems=problems, next_action=next_action,
            difficulty=form_int(difficulty), confidence=form_int(confidence), allow_overlap=True,
        )
        tracking_api.create_session(body, db)
    except Exception as exc:  # validation problems are shown, never swallowed
        return redirect(target, err=f"Couldn't save the activity: {exc}")
    return redirect(target, f"Logged {minutes} min of {kind}.")


@router.get("/search")
def search(request: Request, q: str = "", type: str = "", db: Session = Depends(get_db)):
    results = search_service.search(db, q, [type] if type else None, limit=80) if len(q.strip()) >= 2 else []
    return render(request, "search.html", {"q": q, "type": type, "results": results})
