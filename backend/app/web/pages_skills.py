"""Skills: table, bulk 1–6 self-assessment, detail and edit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import PROFICIENCY_LABELS
from ..routers import skills as skills_api
from ..schemas import SkillCreate, SkillUpdate
from .core import CLASSIFICATION_LABELS, form_int, redirect, render

router = APIRouter(include_in_schema=False)

TRACK_LABELS = {"python": "Python", "backend": "Backend", "ai": "AI", "none": "Other"}
SKILL_STATUSES = ["not_started", "learning", "revising", "confident", "paused"]
EDIT_FIELDS = ("name", "category", "description", "classification", "evidence_notes", "track", "status",
               "next_action", "practical_experience", "notes")


def _consts() -> dict:
    return {"track_labels": TRACK_LABELS, "statuses": SKILL_STATUSES, "classifications": CLASSIFICATION_LABELS,
            "levels": PROFICIENCY_LABELS}


@router.get("/skills")
def skills_page(request: Request, q: str = "", track: str = "", classification: str = "", archived: int = 0,
                db: Session = Depends(get_db)):
    rows = skills_api.list_skills(category=None, status=None, track=track or None, q=q or None,
                                  include_archived=bool(archived), db=db)
    if classification:
        rows = [r for r in rows if r["classification"] == classification]
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)
    rated = [r for r in rows if r["proficiency"]]
    return render(request, "skills.html", {
        "groups": sorted(groups.items()), "count": len(rows), "q": q, "track": track, "classification": classification,
        "archived": archived, "rated": len(rated),
        "gaps": sum(1 for r in rated if r["target_proficiency"] and r["proficiency"] < r["target_proficiency"]),
        **_consts()})


@router.post("/skills/new")
async def skill_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        body = SkillCreate(name=str(form.get("name") or ""), category=str(form.get("category") or "General"),
                           track=str(form.get("track") or "none"),
                           classification=str(form.get("classification") or "to_learn"))
        s = skills_api.create_skill(body, db)
    except (ValidationError, HTTPException) as exc:
        return redirect("/skills", err=str(getattr(exc, "detail", exc))[:300])
    return redirect(f"/skills/{s['id']}", "Skill added.")


@router.get("/skills/assess")
def assess_page(request: Request, track: str = "", db: Session = Depends(get_db)):
    rows = skills_api.list_skills(category=None, status=None, track=track or None, q=None, include_archived=False, db=db)
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)
    return render(request, "skills_assess.html", {"groups": sorted(groups.items()), "track": track, **_consts()})


@router.post("/skills/assess")
async def assess_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    current = {r["id"]: r for r in skills_api.list_skills(category=None, status=None, track=None, q=None,
                                                          include_archived=True, db=db)}
    changed = 0
    for sid, r in current.items():
        if f"p{sid}" not in form:
            continue
        p, t = form_int(form.get(f"p{sid}")), form_int(form.get(f"t{sid}"))
        update = {}
        if p and p != r["proficiency"]:
            update["proficiency"] = p
        if t and t != r["target_proficiency"]:
            update["target_proficiency"] = t
        if update:
            skills_api.update_skill(sid, SkillUpdate(**update), db)
            changed += 1
    return redirect("/skills", f"Self-assessment saved — {changed} skill{'s' if changed != 1 else ''} changed.")


@router.get("/skills/{skill_id}")
def skill_page(skill_id: int, request: Request, edit: int = 0, db: Session = Depends(get_db)):
    s = skills_api.get_skill(skill_id, db)
    return render(request, "skill.html", {"s": s, "edit": edit, **_consts()})


@router.post("/skills/{skill_id}/edit")
async def skill_save(skill_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    data = {k: str(form.get(k)) for k in EDIT_FIELDS if form.get(k) is not None}
    for k in ("proficiency", "target_proficiency", "confidence"):
        if form.get(k) is not None:
            data[k] = form_int(form.get(k))
    if "is_archived" in form:
        data["is_archived"] = form.get("is_archived") == "1"
    try:
        skills_api.update_skill(skill_id, SkillUpdate(**data), db)
    except ValidationError as exc:
        return redirect(f"/skills/{skill_id}?edit=1", err=str(exc.errors()[0]["msg"]))
    return redirect(f"/skills/{skill_id}", "Skill saved.")
