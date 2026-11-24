"""Learning tracks (Python / Backend / AI), knowledge base topics and assessments."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AssessmentAttempt, RoadmapItem, Skill, Topic
from ..routers import knowledge as knowledge_api
from ..schemas import FlashcardIn, TopicCreate, TopicUpdate
from ..services import assessment, progress, runner
from .core import form_int, redirect, render

router = APIRouter(include_in_schema=False)

TRACKS = {
    "python": ("Python Mastery", "Your primary language — strengthen it to senior level and fix the habits found in your code."),
    "backend": ("Backend Engineering", "Extra weight on PostgreSQL and Python backend architecture."),
    "ai": ("AI Engineering", "A practical path to AI / GenAI Engineer roles, building on your backend strengths."),
}


@router.get("/learn")
def learn_index():
    return redirect("/learn/python")


@router.get("/learn/{track}")
def track_page(track: str, request: Request, db: Session = Depends(get_db)):
    if track not in TRACKS:
        raise HTTPException(404, "Unknown track")
    best = {}
    for tid, pct in db.execute(select(AssessmentAttempt.topic_id, AssessmentAttempt.score_pct).where(
            AssessmentAttempt.mode == "assessment")):
        best[tid] = max(pct, best.get(tid, 0))
    items = list(db.scalars(select(RoadmapItem).where(RoadmapItem.track == track).order_by(RoadmapItem.sort_order)))
    modules = []
    for item in items:
        rows = []
        for step in item.steps:
            t = db.get(Topic, step.topic_id) if step.topic_id else None
            rows.append({"step": step, "topic": t, "best": best.get(t.id) if t else None,
                         "questions": len(t.questions) if t else 0})
        done = sum(1 for r in rows if r["topic"] and r["topic"].status in {"learned", "mastered"})
        modules.append({"item": item, "rows": rows, "done": done, "total": len(rows)})
    projects = list(db.scalars(select(RoadmapItem).where(RoadmapItem.track == "projects").order_by(RoadmapItem.sort_order)))
    projects = [p for p in projects if track in p.slug or (track == "backend" and p.slug in ("proj-ingestion-api", "proj-job-queue"))
                or (track == "ai" and p.slug in ("proj-text-to-sql", "proj-local-rag"))]
    areas = [a for a in progress.area_progress(db) if (a["area"] == track) or (track == "backend" and a["area"] in {"postgresql", "backend", "aws"})]
    title, blurb = TRACKS[track]
    return render(request, "track.html", {"track": track, "title": title, "blurb": blurb, "modules": modules,
                                          "projects": projects, "areas": areas})


# --------------------------------------------------------------------------- knowledge base


@router.get("/topics")
def topics(request: Request, q: str = "", track: str = "", status: str = "", skill_id: str = "",
           db: Session = Depends(get_db)):
    rows = knowledge_api.list_topics(q=q or None, skill_id=form_int(skill_id), category=None, status=status or None,
                                     difficulty=None, tag=None, sort="title", db=db)
    if track:
        tracks = dict(db.execute(select(Topic.id, Topic.track)).all())
        rows = [r for r in rows if tracks.get(r["id"]) == track]
    skills = db.execute(select(Skill.id, Skill.name).where(Skill.track != "none").order_by(Skill.name)).all()
    return render(request, "topics.html", {"rows": rows, "q": q, "track": track, "status": status,
                                           "skill_id": skill_id, "skills": skills})


@router.get("/topics/new")
def topic_new(request: Request, db: Session = Depends(get_db)):
    return render(request, "topic_edit.html", {"topic": None, "skills": _skills(db)})


@router.post("/topics/new")
async def topic_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        body = TopicCreate(**_topic_fields(form))
        t = knowledge_api.create_topic(body, db)
    except Exception as exc:
        return redirect("/topics/new", err=str(exc)[:300])
    return redirect(f"/topics/{t['id']}", "Topic created.")


@router.get("/topics/{topic_id}")
def topic_page(topic_id: int, request: Request, db: Session = Depends(get_db)):
    t = knowledge_api.get_topic(topic_id, db)
    topic = db.get(Topic, topic_id)
    item = db.scalar(select(RoadmapItem).join(RoadmapItem.steps).where(RoadmapItem.steps.any(topic_id=topic_id)))
    return render(request, "topic.html", {"t": t, "topic": topic, "module": item,
                                          "pass_pct": assessment.PASS_PCT, "mastery_pct": assessment.MASTERY_PCT})


@router.get("/topics/{topic_id}/edit")
def topic_edit(topic_id: int, request: Request, db: Session = Depends(get_db)):
    return render(request, "topic_edit.html", {"topic": knowledge_api.get_topic(topic_id, db), "skills": _skills(db)})


@router.post("/topics/{topic_id}/edit")
async def topic_save(topic_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    fields = _topic_fields(form)
    fields.pop("status", None)
    try:
        knowledge_api.update_topic(topic_id, TopicUpdate(**fields), db)
    except HTTPException as exc:
        return redirect(f"/topics/{topic_id}/edit", err=str(exc.detail))
    return redirect(f"/topics/{topic_id}", "Saved.")


@router.post("/topics/{topic_id}/status")
def topic_status(topic_id: int, status: str = Form(...), note: str = Form(""), db: Session = Depends(get_db)):
    try:
        knowledge_api.update_topic(topic_id, TopicUpdate(status=status, change_note=note), db)
    except HTTPException as exc:
        return redirect(f"/topics/{topic_id}", err=str(exc.detail))
    except ValueError as exc:
        return redirect(f"/topics/{topic_id}", err=str(exc))
    return redirect(f"/topics/{topic_id}", f"Status set to {status.replace('_', ' ')}.")


@router.post("/topics/{topic_id}/confidence")
def topic_confidence(topic_id: int, confidence: int = Form(...), db: Session = Depends(get_db)):
    knowledge_api.update_topic(topic_id, TopicUpdate(confidence=max(1, min(5, confidence))), db)
    return redirect(f"/topics/{topic_id}")


@router.post("/topics/{topic_id}/notes")
def topic_notes(topic_id: int, notes_md: str = Form(""), db: Session = Depends(get_db)):
    knowledge_api.update_topic(topic_id, TopicUpdate(notes_md=notes_md), db)
    return redirect(f"/topics/{topic_id}#notes", "Notes saved.")


@router.post("/topics/{topic_id}/scratch/run")
def topic_scratch_run(topic_id: int, request: Request, code: str = Form("")):
    result = runner.run_python_script(code) if code.strip() else {"stdout": "", "stderr": "Write some code first.", "timed_out": False}
    return render(request, "partials/scratch_result.html", {"run": result})


@router.post("/topics/{topic_id}/flashcards")
def topic_flashcard(topic_id: int, front: str = Form(...), back: str = Form(...), db: Session = Depends(get_db)):
    knowledge_api.create_flashcard(FlashcardIn(topic_id=topic_id, front=front, back=back, add_to_revision=True), db)
    return redirect(f"/topics/{topic_id}#flashcards", "Flashcard added and scheduled for revision.")


@router.post("/topics/{topic_id}/start")
def topic_start(topic_id: int, db: Session = Depends(get_db)):
    """Start studying: mark learning + start the timer on this topic."""
    from ..routers import tracking as tracking_api
    from ..schemas import TimerStartIn

    topic = db.get(Topic, topic_id)
    if topic and topic.status == "not_started":
        knowledge_api.update_topic(topic_id, TopicUpdate(status="learning"), db)
    tracking_api.start_timer(TimerStartIn(kind="learning", topic_id=topic_id), db)
    return redirect(f"/topics/{topic_id}", "Timer started — status set to learning.")


# --------------------------------------------------------------------------- assessments


@router.get("/topics/{topic_id}/assess")
def assess_form(topic_id: int, request: Request, db: Session = Depends(get_db)):
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(404, "Topic not found")
    if not topic.questions:
        return redirect(f"/topics/{topic_id}", err="This topic has no questions yet — add some on the edit page.")
    return render(request, "assess.html", {"topic": topic, "questions": topic.questions, "started": int(time.time()),
                                           "pass_pct": assessment.PASS_PCT})


def responses_from_form(form, questions) -> list[dict]:
    out = []
    for q in questions:
        out.append({"question_id": q.id, "response": str(form.get(f"q{q.id}", "") or ""),
                    "rubric_ticks": [int(v) for v in form.getlist(f"q{q.id}_tick") if str(v).isdigit()]})
    return out


@router.post("/topics/{topic_id}/assess")
async def assess_submit(topic_id: int, request: Request, db: Session = Depends(get_db)):
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(404, "Topic not found")
    form = await request.form()
    started = form_int(form.get("started"))
    duration = int(time.time()) - started if started else None
    attempt = assessment.record_attempt(db, responses_from_form(form, topic.questions), topic_id=topic_id,
                                        duration_seconds=duration if duration and duration < 86400 else None)
    db.commit()
    return redirect(f"/assessments/{attempt.id}")


@router.get("/assessments/{attempt_id}")
def assess_result(attempt_id: int, request: Request, db: Session = Depends(get_db)):
    from ..models import Question

    r = knowledge_api.assessment_result(attempt_id, db)
    qs = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_([i["question_id"] for i in r["items"]])))}
    return render(request, "assess_result.html", {"r": r, "qs": qs})


# --------------------------------------------------------------------------- helpers


def _skills(db: Session):
    return db.execute(select(Skill.id, Skill.name).order_by(Skill.name)).all()


def _topic_fields(form) -> dict:
    keys = ("title", "category", "difficulty", "summary", "why_md", "details_md", "code_md", "use_cases_md",
            "mistakes_md", "best_practices_md", "my_context_md", "revision_notes_md", "exercises_md", "notes_md", "status")
    out = {k: str(form.get(k)) for k in keys if form.get(k) is not None}
    out["skill_id"] = form_int(form.get("skill_id"))
    if form.get("tags") is not None:
        out["tags"] = [t.strip() for t in str(form.get("tags")).split(",") if t.strip()]
    if not out.get("status"):
        out.pop("status", None)
    return out
