"""Interview prep: question bank by role/category, practice, mock interviews and resume claims (STAR)."""

from __future__ import annotations

import random

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db
from ..enums import ROLES
from ..models import InterviewQuestion, ResumeClaim
from ..routers import interview as interview_api
from ..schemas import ClaimUpdate, PracticeIn
from ..services import revision
from .core import form_int, redirect, render

router = APIRouter(include_in_schema=False)

CLAIM_STATUSES = ["needs-preparation", "partially-supported", "supported", "ready"]


def _roles(db: Session) -> dict[int, list]:
    return dict(db.execute(select(InterviewQuestion.id, InterviewQuestion.roles)).all())


@router.get("/interview")
def interview_page(request: Request, role: str = "", category: str = "", status: str = "", q: str = "",
                   db: Session = Depends(get_db)):
    rows = interview_api.list_questions(category=category or None, difficulty=None, status=status or None,
                                        skill_id=None, project_id=None, q=q or None, db=db)
    if role:
        roles = _roles(db)
        rows = [r for r in rows if role in (roles.get(r["id"]) or [])]
    cats = interview_api.categories(db)
    counts = {s: sum(1 for c in db.scalars(select(InterviewQuestion.status)) if c == s) for s in ("new", "practising", "confident")}
    return render(request, "interview.html", {"rows": rows[:200], "total": len(rows), "role": role, "category": category,
                                              "status": status, "q": q, "roles": ROLES, "cats": cats, "counts": counts})


@router.post("/interview/{qid}/practice")
def interview_practice(qid: int, confidence: int = Form(...), my_answer_md: str = Form(""), next: str = Form("/interview"),
                       db: Session = Depends(get_db)):
    interview_api.practice(qid, PracticeIn(confidence=max(1, min(5, confidence)), my_answer_md=my_answer_md or None), db)
    return redirect(_safe(next), f"Practice recorded (confidence {confidence}/5).")


@router.post("/interview/{qid}/revise")
def interview_revise(qid: int, next: str = Form("/interview"), db: Session = Depends(get_db)):
    try:
        revision.schedule(db, "interview", qid)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return redirect(_safe(next), "Added to revision.")


def _safe(url: str) -> str:
    return url if url.startswith("/") and not url.startswith("//") else "/interview"


# --------------------------------------------------------------------------- mock interview


@router.get("/interview/mock")
def mock_page(request: Request, role: str = "", n: int = 5, db: Session = Depends(get_db)):
    pool = list(db.scalars(select(InterviewQuestion)))
    if role:
        pool = [p for p in pool if role in (p.roles or [])]
    random.shuffle(pool)
    n = max(1, min(n, 15))
    return render(request, "interview_mock.html", {"questions": pool[:n], "role": role, "n": n, "roles": ROLES})


@router.post("/interview/mock")
async def mock_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    role = str(form.get("role") or "")
    ids = [int(v) for v in form.getlist("qid") if str(v).isdigit()]
    scores = []
    for qid in ids:
        conf = form_int(form.get(f"c{qid}"))
        if not conf:
            continue
        answer = str(form.get(f"a{qid}") or "").strip()
        interview_api.practice(qid, PracticeIn(confidence=max(1, min(5, conf)), my_answer_md=answer or None), db)
        scores.append(conf)
    if not scores:
        return redirect(f"/interview/mock?role={role}", err="Rate at least one answer.")
    avg = sum(scores) / len(scores)
    label = ROLES.get(role, "mixed roles")
    history.record(db, "mock_interview", "interview", None,
                   f"Mock interview ({label}): {len(scores)} questions, average confidence {avg:.1f}/5",
                   details={"role": role, "question_ids": ids, "scores": scores, "average": round(avg, 2)})
    db.commit()
    weak = [qid for qid in ids if (form_int(form.get(f"c{qid}")) or 5) <= 2]
    for qid in weak:
        revision.schedule(db, "interview", qid)
    db.commit()
    msg = f"Mock interview saved — average {avg:.1f}/5."
    if weak:
        msg += f" {len(weak)} weak answer{'s' if len(weak) != 1 else ''} added to revision."
    return redirect("/interview", msg)


# --------------------------------------------------------------------------- resume claims


@router.get("/claims")
def claims_page(request: Request, db: Session = Depends(get_db)):
    claims = list(db.scalars(select(ResumeClaim).order_by(ResumeClaim.id)))
    counts = {s: sum(1 for c in claims if c.status == s) for s in CLAIM_STATUSES}
    return render(request, "claims.html", {"claims": claims, "statuses": CLAIM_STATUSES, "counts": counts})


@router.post("/claims/{claim_id}")
async def claim_save(claim_id: int, request: Request, db: Session = Depends(get_db)):
    claim = db.get(ResumeClaim, claim_id)
    if claim is None:
        raise HTTPException(404, "Claim not found")
    form = await request.form()
    fields = ("status", "star_situation", "star_task", "star_action", "star_result", "evidence_md")
    try:
        body = ClaimUpdate(**{k: str(form.get(k)) for k in fields if form.get(k) is not None})
    except ValidationError as exc:
        return redirect(f"/claims#claim-{claim_id}", err=str(exc.errors()[0]["msg"]))
    data = body.model_dump(exclude_unset=True)
    history.record_changes(db, claim, data, entity_type="claim", label=claim.claim[:60], tracked=("status",))
    db.commit()
    return redirect(f"/claims#claim-{claim_id}", "Claim saved.")
