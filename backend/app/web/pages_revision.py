"""Spaced revision: queue, one-at-a-time session, and a quick mixed quiz."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Question, ReviewItem, Skill
from ..routers import knowledge as knowledge_api
from ..routers import revision as revision_api
from ..schemas import RateIn
from ..services import assessment
from .core import form_int, redirect, render
from .pages_learn import responses_from_form

router = APIRouter(include_in_schema=False)

ITEM_TYPES = [("", "All"), ("topic", "Topics"), ("flashcard", "Flashcards"), ("question", "Questions"),
              ("interview", "Interview")]
RATINGS = [("forgot", "Forgot", "bad"), ("difficult", "Difficult", "warn"),
           ("need_practice", "Need practice", ""), ("easy", "Easy", "good")]


def _due(db: Session, item_type: str) -> list[dict]:
    q = revision_api.queue(skill_id=None, item_type=item_type or None, days_ahead=0, db=db)
    return q["overdue"] + q["due_today"]


def _session_url(item_type: str, done: int) -> str:
    return f"/revision/session?type={item_type}&done={done}"


@router.get("/revision")
def revision_page(request: Request, type: str = "", db: Session = Depends(get_db)):
    q = revision_api.queue(skill_id=None, item_type=type or None, days_ahead=7, db=db)
    stats = revision_api.revision_stats(db=db)
    return render(request, "revision.html", {"q": q, "stats": stats, "type": type, "item_types": ITEM_TYPES})


@router.post("/revision/items/{item_id}/pause")
def pause_item(item_id: int, db: Session = Depends(get_db)):
    revision_api.unschedule(item_id, db)
    return redirect("/revision", "Item paused — schedule it again from its page to resume.")


@router.get("/revision/session")
def session_page(request: Request, type: str = "", done: int = 0, db: Session = Depends(get_db)):
    due = _due(db, type)
    item = due[0] if due else None
    return render(request, "revision_session.html", {
        "item": item, "remaining": len(due), "done": done, "type": type, "item_types": ITEM_TYPES,
        "ratings": RATINGS, "started": int(time.time())})


def _duration(started: str | None) -> int | None:
    s = form_int(started)
    d = int(time.time()) - s if s else None
    return d if d is not None and 0 <= d < 86400 else None


@router.post("/revision/session/{item_id}/rate")
def session_rate(item_id: int, rating: str = Form(...), type: str = Form(""), done: int = Form(0),
                 started: str = Form(""), db: Session = Depends(get_db)):
    try:
        out = revision_api.rate(item_id, RateIn(rating=rating, duration_seconds=_duration(started)), db)
    except (HTTPException, ValueError) as exc:
        return redirect(_session_url(type, done), err=str(getattr(exc, "detail", exc))[:300])
    return redirect(_session_url(type, done + 1), f"{rating.replace('_', ' ').capitalize()} — next in "
                                                   f"{out['interval_days']} day{'s' if out['interval_days'] != 1 else ''}.")


@router.post("/revision/session/{item_id}/answer")
async def session_answer(item_id: int, request: Request, db: Session = Depends(get_db)):
    """Grade a `question` item. Wrong → rated forgot automatically; right → the user picks easy / need practice."""
    item = db.get(ReviewItem, item_id)
    if item is None or item.item_type != "question":
        raise HTTPException(404, "Question review item not found")
    q = db.get(Question, item.item_id)
    form = await request.form()
    resp = responses_from_form(form, [q])[0]
    g = assessment.grade(q, resp["response"], resp["rubric_ticks"])
    type_, done = str(form.get("type") or ""), form_int(form.get("done")) or 0
    revealed = {**g, "question_id": q.id, "type": q.type, "response": resp["response"]}
    auto_rating = None
    if g["correct"] is not True:
        auto_rating = "forgot" if g["correct"] is False else "difficult"
        revision_api.rate(item_id, RateIn(rating=auto_rating, duration_seconds=_duration(form.get("started"))), db)
        done += 1
    return render(request, "revision_answer.html", {
        "item": item, "q": q, "revealed": revealed, "auto_rating": auto_rating, "type": type_, "done": done,
        "started": form.get("started") or "", "ratings": [r for r in RATINGS if r[0] in ("need_practice", "easy")]})


# --------------------------------------------------------------------------- quick quiz


@router.get("/revision/quiz")
def quiz_page(request: Request, track: str = "", skill_id: str = "", limit: int = 10, weak: int = 0,
              db: Session = Depends(get_db)):
    if weak:  # most-missed questions from past attempts, topped up with random ones
        ids = assessment.weak_question_ids(db, limit)
        if len(ids) < limit:
            extra = knowledge_api.mixed_quiz(skill_id=None, track=None, limit=limit * 2, auto_only=True, db=db)
            ids += [p["id"] for p in extra if p["id"] not in ids][: limit - len(ids)]
    else:
        picked = knowledge_api.mixed_quiz(skill_id=form_int(skill_id), track=track or None, limit=limit, auto_only=True, db=db)
        ids = [p["id"] for p in picked]
    by_id = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(ids)))} if ids else {}
    skills = db.execute(select(Skill.id, Skill.name).where(Skill.track != "none").order_by(Skill.name)).all()
    return render(request, "quiz.html", {"questions": [by_id[i] for i in ids if i in by_id], "track": track,
                                         "skill_id": skill_id, "limit": limit, "skills": skills, "weak": bool(weak),
                                         "started": int(time.time())})


@router.post("/revision/quiz")
async def quiz_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    ids = [int(v) for v in form.getlist("qid") if str(v).isdigit()]
    questions = list(db.scalars(select(Question).where(Question.id.in_(ids)))) if ids else []
    if not questions:
        return redirect("/revision/quiz", err="No questions to grade.")
    attempt = assessment.record_attempt(db, responses_from_form(form, questions), mode="quiz",
                                        duration_seconds=_duration(form.get("started")))
    db.commit()
    return redirect(f"/assessments/{attempt.id}")
