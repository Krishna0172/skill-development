"""Coding practice: exercise list, editor page, run (HTMX partial) and submit."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import EXERCISE_CATEGORY_LABELS
from ..routers import practice as practice_api
from ..schemas import AttemptIn, RunIn
from .core import form_int, redirect, render

router = APIRouter(include_in_schema=False)


@router.get("/practice")
def practice_list(request: Request, q: str = "", status: str = "", difficulty: str = "", db: Session = Depends(get_db)):
    rows = practice_api.list_exercises(category=None, difficulty=difficulty or None, language=None,
                                       status=status or None, skill_id=None, q=q or None, db=db)
    rows = [r for r in rows if r["language"] != "javascript"]
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)
    ordered = [(c, EXERCISE_CATEGORY_LABELS.get(c, c), groups[c]) for c in
               [*EXERCISE_CATEGORY_LABELS, *sorted(set(groups) - set(EXERCISE_CATEGORY_LABELS))] if c in groups]
    all_rows = practice_api.list_exercises(category=None, difficulty=None, language=None, status=None,
                                           skill_id=None, q=None, db=db)
    counts = {s: sum(1 for r in all_rows if r["status"] == s) for s in ("pending", "attempted", "solved")}
    return render(request, "practice.html", {"groups": ordered, "q": q, "status": status, "difficulty": difficulty,
                                             "count": len(rows), "counts": counts})


def _exercise_ctx(db: Session, exercise_id: int, *, solution: bool = False, code: str | None = None,
                  run: dict | None = None) -> dict:
    e = practice_api.get_exercise(exercise_id, db)
    sol = None
    if solution and e["solution_available"]:
        sol = practice_api.get_solution(exercise_id, db)
    last_code = e["attempt_history"][0]["code"] if e["attempt_history"] else None
    return {"e": e, "solution": sol, "code": code if code is not None else (last_code or e["starter_code"] or ""),
            "run": run, "started": int(time.time()), "category_label": EXERCISE_CATEGORY_LABELS.get(e["category"], e["category"])}


_TESTS_HINT = {
    "python": '{"function": "add", "cases": [{"args": [1, 2], "expected": 3}, {"args": ["x"], "raises": "TypeError"}]}',
    "sql": '{"schema_sql": "CREATE TABLE t(a INT); INSERT INTO t VALUES (1);", "expected": {"columns": ["a"], "rows": [[1]]}, "ordered": false}',
}


def _editor_ctx(db: Session, e=None, form: dict | None = None) -> dict:
    import json as _json

    from sqlalchemy import select as _select

    from ..models import Skill

    skills = db.execute(_select(Skill.id, Skill.name).order_by(Skill.name)).all()
    f = form or ({} if e is None else {
        "title": e.title, "category": e.category, "language": e.language, "difficulty": e.difficulty,
        "problem_md": e.problem_md, "starter_code": e.starter_code, "solution_code": e.solution_code,
        "solution_md": e.solution_md, "hints": "\n".join(e.hints or []),
        "tests": _json.dumps(e.tests, indent=2) if e.tests else "",
    })
    return {"e": e, "f": f, "categories": EXERCISE_CATEGORY_LABELS, "skills": skills, "tests_hint": _TESTS_HINT}


@router.get("/practice/new")
def exercise_new(request: Request, db: Session = Depends(get_db)):
    return render(request, "exercise_edit.html", _editor_ctx(db))


@router.get("/practice/{exercise_id}/edit")
def exercise_edit(exercise_id: int, request: Request, db: Session = Depends(get_db)):
    e = practice_api.get_or_404(db, practice_api.Exercise, exercise_id, "Exercise")
    return render(request, "exercise_edit.html", _editor_ctx(db, e))


@router.post("/practice/save")
async def exercise_save(request: Request, db: Session = Depends(get_db)):
    import json as _json

    from ..schemas import ExerciseIn, ExerciseUpdate
    from ..services import runner

    form = await request.form()
    f = {k: str(form.get(k) or "") for k in ("title", "category", "language", "difficulty", "problem_md",
                                              "starter_code", "solution_code", "solution_md", "hints", "tests")}
    eid = form_int(form.get("id"))
    e = practice_api.get_or_404(db, practice_api.Exercise, eid, "Exercise") if eid else None
    tests = None
    if f["tests"].strip():
        try:
            tests = _json.loads(f["tests"])
            if not isinstance(tests, dict):
                raise ValueError("tests must be a JSON object")
        except ValueError as exc:
            return render(request, "exercise_edit.html", {**_editor_ctx(db, e, f), "error": f"Tests JSON is invalid: {exc}"},
                          status_code=422)
    payload = {"title": f["title"].strip(), "category": f["category"], "difficulty": f["difficulty"],
               "problem_md": f["problem_md"], "starter_code": f["starter_code"], "solution_code": f["solution_code"],
               "solution_md": f["solution_md"], "hints": [h.strip() for h in f["hints"].splitlines() if h.strip()],
               "skill_id": form_int(form.get("skill_id")), "tests": tests}
    language = e.language if e else f["language"]
    try:
        if e:
            practice_api.update_exercise(e.id, ExerciseUpdate(**payload), db)
            new_id = e.id
        else:
            new_id = practice_api.create_exercise(ExerciseIn(language=language, **payload), db)["id"]
    except Exception as exc:  # validation / bad refs: show the form again with the message
        db.rollback()
        return render(request, "exercise_edit.html", {**_editor_ctx(db, e, f), "error": str(exc)[:400]}, status_code=422)
    note = "Exercise saved."
    if tests and payload["solution_code"].strip() and language in {"python", "sql"}:
        check = runner.run(language, payload["solution_code"], tests)
        note += " Your solution passes all tests." if check["passed"] else (
            f" Warning: your own solution fails the tests ({check['tests_passed']}/{check['tests_total']})"
            + (f" - {check['error']}" if check.get("error") else "") + ".")
    return redirect(f"/practice/{new_id}", note[:300])


@router.get("/practice/{exercise_id}")
def exercise_page(exercise_id: int, request: Request, solution: int = 0, db: Session = Depends(get_db)):
    return render(request, "exercise.html", _exercise_ctx(db, exercise_id, solution=bool(solution)))


@router.post("/practice/{exercise_id}/run")
async def exercise_run(exercise_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    code = str(form.get("code") or "")
    result = practice_api.run_code(exercise_id, RunIn(code=code), db)
    if request.headers.get("HX-Request"):
        return render(request, "partials/run_result.html", {"run": result})
    return render(request, "exercise.html", _exercise_ctx(db, exercise_id, code=code, run=result))


@router.post("/practice/{exercise_id}/scratch")
async def exercise_scratch(exercise_id: int, request: Request, db: Session = Depends(get_db)):
    """Run the editor contents as a plain script (no tests) so print() debugging works."""
    from ..services import runner

    practice_api.get_or_404(db, practice_api.Exercise, exercise_id, "Exercise")
    form = await request.form()
    code = str(form.get("code") or "")
    result = runner.run_python_script(code) if code.strip() else {"stdout": "", "stderr": "Write some code first.", "timed_out": False}
    return render(request, "partials/scratch_result.html", {"run": result})


@router.get("/playground")
def playground(request: Request):
    return render(request, "playground.html", {})


@router.post("/playground/run")
async def playground_run(request: Request):
    from ..services import runner

    form = await request.form()
    code, stdin = str(form.get("code") or ""), str(form.get("stdin") or "")
    result = runner.run_python_script(code, stdin) if code.strip() else {"stdout": "", "stderr": "Write some code first.", "timed_out": False}
    return render(request, "partials/scratch_result.html", {"run": result})


@router.post("/practice/{exercise_id}/submit")
async def exercise_submit(exercise_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    started = form_int(form.get("started"))
    duration = int(time.time()) - started if started else None
    give_up = form.get("give_up") == "1"
    try:
        body = AttemptIn(
            code=str(form.get("code") or ""),
            self_outcome="gave_up" if give_up else (str(form.get("self_outcome") or "") or None),
            duration_seconds=duration if duration is not None and 0 <= duration < 86400 else None,
            hints_used=min(20, form_int(form.get("hints_used")) or 0),
            learned_md=str(form.get("learned_md") or ""),
            notes=str(form.get("notes") or ""),
            retry_needed=form.get("retry_needed") == "1",
        )
        out = practice_api.submit_attempt(exercise_id, body, db)
    except HTTPException as exc:
        return redirect(f"/practice/{exercise_id}", err=str(exc.detail))
    except ValueError as exc:
        return redirect(f"/practice/{exercise_id}", err=str(exc)[:300])
    if give_up:
        return redirect(f"/practice/{exercise_id}?solution=1#solution", "Recorded as gave up — study the solution, then retry later.")
    a = out["attempt"]
    msg = a["outcome"].replace("_", " ").capitalize()
    if a["tests_total"]:
        msg += f" — {a['tests_passed']}/{a['tests_total']} tests"
    if out["first_solve"]:
        msg += ". First solve! 🎉"
    return redirect(f"/practice/{exercise_id}#attempts", msg)
