"""Coding practice: exercises, running code, attempts, and gated solutions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..db import get_db
from ..enums import SOLVED_OUTCOMES
from ..models import Exercise, ExerciseAttempt, Skill, Topic
from ..schemas import AttemptIn, ExerciseIn, ExerciseUpdate, RunIn
from ..services import runner, topics as topic_service
from .common import check_refs, columns, get_or_404, iso
from .knowledge import _unique_slug

router = APIRouter(tags=["practice"])


def _status(attempts: list[ExerciseAttempt]) -> str:
    if any(a.outcome in SOLVED_OUTCOMES for a in attempts):
        return "solved"
    return "attempted" if attempts else "pending"


def _brief(e: Exercise, skills: dict[int, str]) -> dict:
    return {
        **columns(e, "id", "slug", "title", "category", "language", "difficulty", "skill_id", "concepts", "source"),
        "skill": skills.get(e.skill_id),
        "status": _status(e.attempts),
        "attempts": len(e.attempts),
        "auto_check": bool(e.tests) and e.language in {"python", "sql"},
        "retry_needed": bool(e.attempts) and e.attempts[-1].retry_needed,
        "last_attempt_at": iso(e.attempts[-1].submitted_at) if e.attempts else None,
    }


def _attempt_out(a: ExerciseAttempt) -> dict:
    return columns(a, "id", "submitted_at", "language", "code", "outcome", "tests_passed", "tests_total",
                   "duration_seconds", "hints_used", "solution_viewed", "solved_independently", "retry_needed",
                   "learned_md", "notes")


@router.get("/exercises")
def list_exercises(category: str | None = None, difficulty: str | None = None, language: str | None = None,
                   status: str | None = None, skill_id: int | None = None, q: str | None = None,
                   db: Session = Depends(get_db)):
    stmt = select(Exercise).order_by(Exercise.category, Exercise.difficulty, Exercise.title)
    if category:
        stmt = stmt.where(Exercise.category == category)
    if difficulty:
        stmt = stmt.where(Exercise.difficulty == difficulty)
    if language:
        stmt = stmt.where(Exercise.language == language)
    if skill_id:
        stmt = stmt.where(Exercise.skill_id == skill_id)
    if q:
        stmt = stmt.where(Exercise.title.ilike(f"%{q}%") | Exercise.problem_md.ilike(f"%{q}%"))
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    out = [_brief(e, skills) for e in db.scalars(stmt)]
    if status:
        out = [e for e in out if e["status"] == status]
    order = {"easy": 0, "medium": 1, "hard": 2}
    out.sort(key=lambda e: (e["category"], order.get(e["difficulty"], 3), e["title"]))
    return out


@router.get("/exercises/{exercise_id}")
def get_exercise(exercise_id: int, db: Session = Depends(get_db)):
    """Everything except the solution and the test expectations (no spoilers)."""
    e = get_or_404(db, Exercise, exercise_id, "Exercise")
    skills = dict(db.execute(select(Skill.id, Skill.name)).all())
    return {
        **_brief(e, skills),
        **columns(e, "problem_md", "examples", "hints", "starter_code"),
        "test_count": runner._count_cases(e.language, e.tests) if e.tests else 0,
        "topics": [{"id": t.id, "title": t.title} for t in e.topics],
        "attempt_history": [_attempt_out(a) for a in reversed(e.attempts)],
        "solution_available": bool(e.attempts),
        "node_available": runner.node_available() if e.language == "javascript" else None,
    }


@router.get("/exercises/{exercise_id}/solution")
def get_solution(exercise_id: int, db: Session = Depends(get_db)):
    e = get_or_404(db, Exercise, exercise_id, "Exercise")
    if not e.attempts:
        raise HTTPException(403, "Attempt the exercise first (submit once, or choose 'Give up & show solution').")
    last = e.attempts[-1]
    if not last.solution_viewed:
        last.solution_viewed = True
        db.commit()
    return {"solution_code": e.solution_code, "solution_md": e.solution_md}


@router.post("/exercises/{exercise_id}/run")
def run_code(exercise_id: int, body: RunIn, db: Session = Depends(get_db)):
    """Run against the tests WITHOUT recording an attempt (like a scratch run)."""
    e = get_or_404(db, Exercise, exercise_id, "Exercise")
    return runner.run(e.language, body.code, e.tests)


@router.post("/exercises/{exercise_id}/attempts", status_code=201)
def submit_attempt(exercise_id: int, body: AttemptIn, db: Session = Depends(get_db)):
    e = get_or_404(db, Exercise, exercise_id, "Exercise")
    if body.client_ref and db.scalar(select(ExerciseAttempt.id).where(ExerciseAttempt.client_ref == body.client_ref)):
        raise HTTPException(409, "This attempt was already recorded.")
    was_solved = any(a.outcome in SOLVED_OUTCOMES for a in e.attempts)
    result = None
    auto = bool(e.tests) and e.language in {"python", "sql"} and body.self_outcome is None
    if auto:
        result = runner.run(e.language, body.code, e.tests)
        if "Node.js is not installed" in (result.get("error") or ""):
            raise HTTPException(422, result["error"])
        outcome = "passed" if result["passed"] else ("error" if result.get("error") else "failed")
    else:
        if body.self_outcome is None:
            raise HTTPException(422, "This exercise has no automatic tests — choose a self-assessed outcome.")
        outcome = body.self_outcome
    attempt = ExerciseAttempt(
        client_ref=body.client_ref, exercise_id=e.id, language=e.language, code=body.code, outcome=outcome,
        tests_passed=result["tests_passed"] if result else None, tests_total=result["tests_total"] if result else None,
        duration_seconds=body.duration_seconds, hints_used=body.hints_used, notes=body.notes,
        learned_md=body.learned_md, retry_needed=body.retry_needed,
        solved_independently=outcome in SOLVED_OUTCOMES and body.hints_used == 0 and not any(a.solution_viewed for a in e.attempts),
    )
    e.attempts.append(attempt)  # via the relationship so e.attempts reflects it immediately
    db.flush()
    history.record(db, "attempt", "exercise", e.id,
                   f"{e.title}: {outcome.replace('_', ' ')}" + (f" ({attempt.tests_passed}/{attempt.tests_total} tests)" if result else ""),
                   skill_id=e.skill_id, details={"outcome": outcome, "attempt_id": attempt.id})
    # Solving an exercise is practice for its topics: move not-started/learning topics to "practised"
    if outcome in SOLVED_OUTCOMES and not was_solved:
        for t in e.topics:
            if t.status in {"not_started", "learning"}:
                topic_service.set_status(db, t, "practised", f"solved exercise: {e.title}")
    db.commit()
    return {"attempt": _attempt_out(attempt), "result": result, "status": _status(e.attempts),
            "first_solve": outcome in SOLVED_OUTCOMES and not was_solved}


@router.post("/exercises", status_code=201)
def create_exercise(body: ExerciseIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    check_refs(db, data)
    topic_ids = data.pop("topic_ids")
    e = Exercise(slug=_unique_slug(db, Exercise, body.title), source="user", **data)
    e.topics = list(db.scalars(select(Topic).where(Topic.id.in_(topic_ids)))) if topic_ids else []
    db.add(e)
    db.commit()
    return get_exercise(e.id, db)


@router.patch("/exercises/{exercise_id}")
def update_exercise(exercise_id: int, body: ExerciseUpdate, db: Session = Depends(get_db)):
    e = get_or_404(db, Exercise, exercise_id, "Exercise")
    data = body.model_dump(exclude_unset=True)
    check_refs(db, data)
    if "topic_ids" in data:
        ids = data.pop("topic_ids") or []
        e.topics = list(db.scalars(select(Topic).where(Topic.id.in_(ids)))) if ids else []
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    return get_exercise(e.id, db)


@router.delete("/exercises/{exercise_id}", status_code=204)
def delete_exercise(exercise_id: int, db: Session = Depends(get_db)):
    e = get_or_404(db, Exercise, exercise_id, "Exercise")
    history.record(db, "deleted", "exercise", e.id, f"Deleted exercise {e.title}", skill_id=e.skill_id)
    db.delete(e)
    db.commit()


@router.delete("/attempts/{attempt_id}", status_code=204)
def delete_attempt(attempt_id: int, db: Session = Depends(get_db)):
    """Remove an incorrect attempt record (e.g. accidental submit)."""
    a = get_or_404(db, ExerciseAttempt, attempt_id, "Attempt")
    history.record(db, "deleted", "attempt", a.id, f"Deleted attempt #{a.id} ({a.outcome})")
    db.delete(a)
    db.commit()
