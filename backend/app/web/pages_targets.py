"""Targets (daily/weekly/monthly) and longer-term goals."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import RoadmapItem, Skill, Target
from ..routers import goals as goals_api
from ..schemas import GoalCreate, GoalUpdate, TargetIn
from ..services import progress
from .core import form_int, redirect, render

router = APIRouter(include_in_schema=False)

PERIODS = ["daily", "weekly", "monthly"]
GOAL_METRICS = {"manual": "Manual %", "exercises_solved": "Exercises solved", "topics_learned": "Topics learned",
                "hours": "Hours studied", "reviews_done": "Revisions done", "roadmap_item": "Roadmap project steps"}
GOAL_STATUSES = ["not_started", "in_progress", "completed", "abandoned"]


def _err(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return str(exc.errors()[0]["msg"])
    return str(getattr(exc, "detail", exc))[:300]


@router.get("/targets")
def targets_page(request: Request, closed: int = 0, db: Session = Depends(get_db)):
    live = {t["id"]: t for t in progress.targets_progress(db)}
    targets = sorted(db.scalars(select(Target)), key=lambda t: (PERIODS.index(t.period), t.metric))
    goals = goals_api.list_goals(status=None, db=db)
    open_goals = [g for g in goals if g["status"] not in {"completed", "abandoned"}]
    return render(request, "targets.html", {
        "targets": targets, "live": live, "periods": PERIODS, "metric_labels": progress.METRIC_LABELS,
        "goals": goals if closed else open_goals, "closed": closed, "closed_count": len(goals) - len(open_goals),
        "goal_metrics": GOAL_METRICS, "goal_statuses": GOAL_STATUSES,
        "skills": db.execute(select(Skill.id, Skill.name).order_by(Skill.name)).all(),
        "roadmap_projects": db.execute(select(RoadmapItem.id, RoadmapItem.title)
                                       .where(RoadmapItem.track == "projects").order_by(RoadmapItem.sort_order)).all()})


@router.post("/targets")
def target_save(period: str = Form(...), metric: str = Form(...), value: str = Form(...), is_active: str = Form("1"),
                db: Session = Depends(get_db)):
    try:
        body = TargetIn(period=period, metric=metric, value=value, is_active=is_active == "1")
    except ValidationError as exc:
        return redirect("/targets", err=_err(exc))
    t = db.scalar(select(Target).where(Target.period == body.period, Target.metric == body.metric))
    if t is None:
        db.add(Target(**body.model_dump()))
    else:
        t.value, t.is_active = body.value, body.is_active
    db.commit()
    return redirect("/targets", "Target saved.")


@router.post("/targets/{target_id}/delete")
def target_delete(target_id: int, db: Session = Depends(get_db)):
    t = db.get(Target, target_id)
    if t:
        db.delete(t)
        db.commit()
    return redirect("/targets", "Target removed.")


# --------------------------------------------------------------------------- goals


@router.post("/goals/new")
async def goal_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    metric = str(form.get("metric") or "manual")
    data = {"title": str(form.get("title") or ""), "description": str(form.get("description") or ""),
            "priority": str(form.get("priority") or "medium"), "metric": metric,
            "skill_id": form_int(form.get("skill_id")), "target_date": str(form.get("target_date") or "") or None}
    if metric == "roadmap_item":
        data["metric_filter"] = {"roadmap_item_id": form_int(form.get("roadmap_item_id"))}
    elif metric != "manual":
        data["metric_target"] = str(form.get("metric_target") or "") or None
    try:
        goals_api.create_goal(GoalCreate(**data), db)
    except (ValidationError, HTTPException) as exc:
        return redirect("/targets#goals", err=_err(exc))
    return redirect("/targets#goals", "Goal added.")


@router.post("/goals/{goal_id}")
def goal_update(goal_id: int, status: str = Form(""), manual_progress: str = Form(""), db: Session = Depends(get_db)):
    data: dict = {}
    if status:
        data["status"] = status
    if manual_progress != "":
        data["manual_progress"] = form_int(manual_progress)
    try:
        goals_api.update_goal(goal_id, GoalUpdate(**data), db)
    except (ValidationError, HTTPException) as exc:
        return redirect("/targets#goals", err=_err(exc))
    return redirect(f"/targets#goal-{goal_id}", "Goal updated.")


@router.post("/goals/{goal_id}/delete")
def goal_delete(goal_id: int, db: Session = Depends(get_db)):
    goals_api.delete_goal(goal_id, db)
    return redirect("/targets#goals", "Goal deleted.")
