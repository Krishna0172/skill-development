"""Projects: list, detail with markdown sections, work items and linked skills/topics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ProjectItem, Skill, Topic
from ..routers import projects as projects_api
from ..schemas import ProjectCreate, ProjectItemIn, ProjectItemUpdate, ProjectUpdate
from .core import redirect, render

router = APIRouter(include_in_schema=False)

SECTIONS = [("purpose_md", "Purpose"), ("architecture_md", "Architecture"), ("folder_structure_md", "Folder structure"),
            ("modules_md", "Modules"), ("database_md", "Database"), ("apis_md", "APIs"),
            ("business_logic_md", "Business logic"), ("important_files_md", "Important files"),
            ("important_functions_md", "Important functions"), ("challenges_md", "Challenges"),
            ("solutions_md", "Solutions"), ("contributions_md", "My contributions"), ("learnings_md", "Learnings"),
            ("revise_md", "What I need to revise"), ("pending_improvements_md", "Pending improvements")]
STATUSES = ["planned", "active", "paused", "completed", "archived", "unknown"]
ITEM_KINDS = ["module", "task", "feature", "bug", "improvement"]
ITEM_STATUSES = ["todo", "in_progress", "done"]


def _err(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return str(exc.errors()[0]["msg"])
    return str(getattr(exc, "detail", exc))[:300]


@router.get("/projects")
def projects_page(request: Request, status: str = "", db: Session = Depends(get_db)):
    rows = projects_api.list_projects(kind=None, status=status or None, db=db)
    groups = [(k, label, [r for r in rows if r["kind"] == k]) for k, label in
              (("professional", "Work projects"), ("personal", "Personal projects"))]
    return render(request, "projects.html", {"groups": groups, "status": status, "statuses": STATUSES})


@router.post("/projects/new")
def project_create(name: str = Form(...), kind: str = Form("personal"), summary: str = Form(""),
                   db: Session = Depends(get_db)):
    try:
        p = projects_api.create_project(ProjectCreate(name=name, kind=kind, summary=summary), db)
    except (ValidationError, HTTPException) as exc:
        return redirect("/projects", err=_err(exc))
    return redirect(f"/projects/{p['id']}?edit=1", "Project created — fill in what you know.")


@router.get("/projects/{project_id}")
def project_page(project_id: int, request: Request, edit: int = 0, db: Session = Depends(get_db)):
    p = projects_api.get_project(project_id, db)
    ctx = {"p": p, "edit": edit, "sections": SECTIONS, "statuses": STATUSES, "item_kinds": ITEM_KINDS,
           "item_statuses": ITEM_STATUSES}
    if edit:
        ctx["all_skills"] = db.execute(select(Skill.id, Skill.name).order_by(Skill.name)).all()
        ctx["all_topics"] = db.execute(select(Topic.id, Topic.title).order_by(Topic.title)).all()
    return render(request, "project.html", ctx)


@router.post("/projects/{project_id}/edit")
async def project_save(project_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    keys = ["name", "kind", "organization", "role", "status", "summary", "source_path", *[k for k, _ in SECTIONS]]
    data: dict = {k: str(form.get(k)) for k in keys if form.get(k) is not None}
    for k in ("start_date", "end_date"):
        if k in form:
            data[k] = str(form.get(k)) or None
    if "tech" in form:
        data["tech"] = [t.strip() for t in str(form.get("tech")).split(",") if t.strip()]
    if "links" in form:
        data["skill_ids"] = [int(v) for v in form.getlist("skill_ids") if str(v).isdigit()]
        data["topic_ids"] = [int(v) for v in form.getlist("topic_ids") if str(v).isdigit()]
    try:
        projects_api.update_project(project_id, ProjectUpdate(**data), db)
    except (ValidationError, HTTPException) as exc:
        return redirect(f"/projects/{project_id}?edit=1", err=_err(exc))
    return redirect(f"/projects/{project_id}", "Project saved.")


@router.post("/projects/{project_id}/items")
def item_add(project_id: int, kind: str = Form("task"), title: str = Form(...), description: str = Form(""),
             db: Session = Depends(get_db)):
    try:
        projects_api.add_item(project_id, ProjectItemIn(kind=kind, title=title, description=description), db)
    except (ValidationError, HTTPException) as exc:
        return redirect(f"/projects/{project_id}#items", err=_err(exc))
    return redirect(f"/projects/{project_id}#items")


def _item_project(db: Session, item_id: int) -> int:
    it = db.get(ProjectItem, item_id)
    if it is None:
        raise HTTPException(404, "Item not found")
    return it.project_id


@router.post("/project-items/{item_id}")
def item_update(item_id: int, status: str = Form(""), title: str = Form(""), db: Session = Depends(get_db)):
    pid = _item_project(db, item_id)
    data = {k: v for k, v in (("status", status), ("title", title)) if v}
    try:
        projects_api.update_item(item_id, ProjectItemUpdate(**data), db)
    except (ValidationError, HTTPException) as exc:
        return redirect(f"/projects/{pid}#items", err=_err(exc))
    return redirect(f"/projects/{pid}#items")


@router.post("/project-items/{item_id}/delete")
def item_delete(item_id: int, db: Session = Depends(get_db)):
    pid = _item_project(db, item_id)
    projects_api.delete_item(item_id, db)
    return redirect(f"/projects/{pid}#items", "Item deleted.")


@router.post("/projects/{project_id}/delete")
def project_delete(project_id: int, confirm: str = Form(""), db: Session = Depends(get_db)):
    if confirm != "delete":
        return redirect(f"/projects/{project_id}?edit=1", err='Type "delete" to confirm.')
    projects_api.delete_project(project_id, db)
    return redirect("/projects", "Project deleted.")

