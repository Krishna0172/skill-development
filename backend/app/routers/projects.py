from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db, now
from ..models import InterviewQuestion, LearningSession, Project, ProjectItem, Skill, Topic
from ..schemas import ProjectCreate, ProjectItemIn, ProjectItemUpdate, ProjectUpdate
from ..services import timecalc
from .common import columns, get_or_404, iso
from .knowledge import _unique_slug

router = APIRouter(tags=["projects"])

BRIEF = ("id", "slug", "name", "kind", "organization", "role", "status", "start_date", "end_date", "summary", "tech",
         "source", "updated_at")
FULL = BRIEF + ("purpose_md", "architecture_md", "folder_structure_md", "modules_md", "database_md", "apis_md",
                "business_logic_md", "challenges_md", "solutions_md", "contributions_md", "learnings_md",
                "pending_improvements_md", "important_files_md", "important_functions_md", "revise_md",
                "source_path", "evidence_note", "created_at")


def _item_counts(p: Project) -> dict:
    c: Counter = Counter()
    for it in p.items:
        c[f"{it.kind}_total"] += 1
        if it.status == "done":
            c[f"{it.kind}_done"] += 1
    return dict(c)


def _brief(p: Project) -> dict:
    return {**columns(p, *BRIEF), "skills": [{"id": s.id, "name": s.name} for s in p.skills],
            "items": _item_counts(p),
            "items_done": sum(i.status == "done" for i in p.items), "items_total": len(p.items)}


@router.get("/projects")
def list_projects(kind: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Project).order_by(Project.kind, Project.name)
    if kind:
        stmt = stmt.where(Project.kind == kind)
    if status:
        stmt = stmt.where(Project.status == status)
    return [_brief(p) for p in db.scalars(stmt)]


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = get_or_404(db, Project, project_id, "Project")
    sessions = list(db.scalars(select(LearningSession).where(LearningSession.project_id == p.id)))
    interview = list(db.scalars(select(InterviewQuestion).where(InterviewQuestion.project_id == p.id)))
    return {
        **_brief(p), **columns(p, *FULL),
        "topics": [{"id": t.id, "title": t.title, "status": t.status} for t in p.topics],
        "item_list": [columns(i, "id", "kind", "title", "description", "status", "completed_at", "created_at") for i in p.items],
        "hours_logged": round(timecalc.total_seconds(timecalc.session_interval(s, now()) for s in sessions) / 3600, 2),
        "interview": [{"id": q.id, "question": q.question, "difficulty": q.difficulty, "status": q.status} for q in interview],
    }


def _apply(db: Session, p: Project, data: dict) -> None:
    if "skill_ids" in data:
        ids = data.pop("skill_ids") or []
        p.skills = list(db.scalars(select(Skill).where(Skill.id.in_(ids)))) if ids else []
    if "topic_ids" in data:
        ids = data.pop("topic_ids") or []
        p.topics = list(db.scalars(select(Topic).where(Topic.id.in_(ids)))) if ids else []


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    p = Project(slug=_unique_slug(db, Project, body.name), source="user", name=body.name, kind=body.kind, status=body.status)
    _apply(db, p, data)
    for k, v in data.items():
        if v is not None:
            setattr(p, k, v)
    db.add(p)
    db.flush()
    history.record(db, "created", "project", p.id, f"Added project {p.name}")
    db.commit()
    return get_project(p.id, db)


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db)):
    p = get_or_404(db, Project, project_id, "Project")
    data = body.model_dump(exclude_unset=True)
    _apply(db, p, data)
    history.record_changes(db, p, data, entity_type="project", label=p.name, tracked=("status",))
    db.commit()
    return get_project(p.id, db)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    p = get_or_404(db, Project, project_id, "Project")
    history.record(db, "deleted", "project", p.id, f"Deleted project {p.name}")
    db.delete(p)
    db.commit()


@router.post("/projects/{project_id}/items", status_code=201)
def add_item(project_id: int, body: ProjectItemIn, db: Session = Depends(get_db)):
    p = get_or_404(db, Project, project_id, "Project")
    item = ProjectItem(project_id=p.id, completed_at=now() if body.status == "done" else None, **body.model_dump())
    db.add(item)
    db.flush()
    history.record(db, "project_item", "project", p.id, f"{p.name}: added {item.kind} '{item.title}'")
    db.commit()
    return columns(item, "id", "kind", "title", "description", "status", "completed_at", "created_at")


@router.patch("/project-items/{item_id}")
def update_item(item_id: int, body: ProjectItemUpdate, db: Session = Depends(get_db)):
    item = get_or_404(db, ProjectItem, item_id, "Project item")
    data = body.model_dump(exclude_unset=True)
    if data.get("status") and data["status"] != item.status:
        item.completed_at = now() if data["status"] == "done" else None
        project = db.get(Project, item.project_id)
        history.record(db, "project_item", "project", item.project_id,
                       f"{project.name}: {item.kind} '{item.title}' → {data['status'].replace('_', ' ')}",
                       field="status", old_value=item.status, new_value=data["status"])
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    return columns(item, "id", "kind", "title", "description", "status", "completed_at", "created_at")


@router.delete("/project-items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, ProjectItem, item_id, "Project item"))
    db.commit()
