# HANDOFF — DevGrowth v2 (Python-only rebuild)

Read this first in a new session. Context lives in `docs/03-work-repo-assessment.md` (who the user is, the skill gaps, the v2 plan) and `docs/content-spec.md` (the content JSON schema).

## Goal
A local, private, single-user learning platform for a Python backend dev moving towards AI Engineering.
- **Stack:** FastAPI + **Jinja2 templates + a little HTMX** (no React), SQLAlchemy 2, SQLite. Charts are server-rendered SVG.
- **Scope:** no frontend or JavaScript learning content.
- **Privacy:** offline; nothing is sent externally.
- **User rules:** never modify `D:\forcesight\...` (read-only). Keep token use low: no subagents unless needed, targeted reads only.

## Status
### Done and working
- **Data model** (`backend/app/models.py`), v2:
  - Skill has `classification`, 1–6 `proficiency`, `confidence`, `next_action`.
  - Topic has `why_md`, `revision_notes_md`, `exercises_md` and `track`.
  - `Question` (types: mcq / fill_blank / output / debug / explain / qa) and `AssessmentAttempt` replace the old quiz tables.
  - New tables: `Target` and `ResumeClaim`.
  - Extra fields on `ExerciseAttempt`, `LearningSession` and `Project`.
  - `RoadmapItem` gained `area`, `module_no` and `label`.
- **Services:**
  - `services/assessment.py`: grading; `PASS_PCT` 70, `MASTERY_PCT` 85.
  - `services/topics.py`: `check_evidence` / `MasteryEvidenceError`. Learned or mastered requires demonstrated understanding; a manual change passes `require_evidence=True`.
  - `services/revision.py`: supports the `question` item type, and revision ratings can't promote a topic without evidence.
  - `services/progress.py`: area progress, role readiness and target progress.
  - `services/stats.py`: patched for v2.
- **Seed loader** (`backend/app/seed/loader.py`) loads:
  - skills, `curriculum.json` (→ RoadmapItems per area/module), topics and `topic_extras/`;
  - exercises (v1 categories remapped, JS skipped);
  - interview questions (`roles.json` uses `roles`) and `resume_claims.json`;
  - `projects.json` and `projects_work.json` (if present), plus default targets.
  It maps v1 skill slugs through `V1_SKILL_MAP` and re-links roadmap steps to topics that arrive late.
- **Content** (`backend/app/seed/content/`), complete:
  - 93 topics with typed questions;
  - 43 `topic_extras`;
  - 72 exercises;
  - `interview/` (dsa, system-design, scenarios, roles, resume_claims);
  - skills.json (v2), curriculum.json.
  - The archived v1 seed is in `seed/content_v1_archive/`.
- **JSON API routers** (`backend/app/routers/*`): patched for v2. Knowledge now has `/api/questions`, `/api/quiz/questions` and `/api/assessments`. Web pages call these router functions directly as Python functions.
- **Web layer:**
  - `web/core.py`: templates, filters (`md`, `date`, `rel`, `minutes`, `hours`, `label`), globals (`badge`, `progress`, `dots`, `code_block`, `charts`), `render()`, `redirect(url, msg, err)`, `form_int`. `render()` injects `due_count`, `timer` and `quick_topics`.
  - `web/charts.py`: SVG `bars`, `line`, `hbars`, `segments`, `heatmap`, `ring`.
  - `static/app.css` (dark/light), `static/htmx.min.js` (vendored), `/static/pygments.css` (generated).
  - `templates/base.html` (sidebar, search, timer, quick-add popover, theme cookie via `/theme`), `macros.html` (card, stat, kv, empty, chips, **question_form**), `partials/quick_add.html`, `error.html`.
  - `web/pages_daily.py`: `/` (Today), `/dashboard`, `/theme`, `/timer/start|stop`, `/tracker/quick-add`, `/search`. **Tested: all return 200.**

- `web/pages_learn.py` (wired): `/learn/{python|backend|ai}`, `/topics`, `/topics/new`, `/topics/{id}`, edit, status/confidence/notes/flashcards/start posts, `/topics/{id}/assess`, `/assessments/{id}`. Templates: `track.html`, `topics.html`, `topic.html`, `topic_edit.html`, `assess.html`, `assess_result.html`. **Tested: all GETs 200, all POSTs redirect correctly, assessment submit → result works.**
- `search.html` added (was missing; `/search` used to 500). `services/search.py` URLs moved to v2 paths (`/topics/{id}`, `/learn/{track}#slug`).
- `core.redirect()` now puts `?msg=` before a `#fragment`.
- `web/pages_revision.py` (wired): `/revision` (queue + stats, type chips, pause), `/revision/session` (one due item at a time; `done` counter in query), `/revision/session/{id}/rate`, `/revision/session/{id}/answer` (question items: wrong → auto `forgot`, partial self-grade → auto `difficult`, right → user picks easy / need practice), `/revision/quiz` GET/POST (`mode="quiz"`, result shown on `/assessments/{id}`). Templates: `revision.html`, `revision_session.html`, `revision_answer.html`, `quiz.html`. `.btn.good/.warn/.bad` added to CSS. **Tested end to end.**

- `web/pages_practice.py` (wired): `/practice` (grouped by `EXERCISE_CATEGORY_LABELS`, filters, JS exercises hidden), `/practice/{id}` (`?solution=1` shows the solution once unlocked and marks it viewed), `/practice/{id}/run` (HTMX → `partials/run_result.html`; full page without `HX-Request`), `/practice/{id}/submit` (auto-checked, `self_outcome`, or `give_up=1` → solution). Hints are `details.hint`; an inline `ontoggle` counts opened hints into `hints_used`. Templates: `practice.html`, `exercise.html`, `partials/run_result.html`. **Tested: Python + SQL run/submit/give-up/solve, self-assessed submit.** No exercise editor yet (create/edit via API only).

- **All remaining pages built and wired** (step 5 was split into several modules instead of one `pages_track.py`):
  - `pages_interview.py`: `/interview` (role/category/status/search filters, practise with confidence 1–5, add to revision), `/interview/mock` (N random questions; saved as a `mock_interview` history event; answers rated 1–2 are scheduled for revision), `/claims` (STAR form, `ClaimUpdate`).
  - `pages_skills.py`: `/skills` table, `/skills/assess` bulk 1–6 rating (changes logged to history), `/skills/{id}` detail plus `?edit=1`, `/skills/new`.
  - `pages_projects.py`: `/projects` list and new, `/projects/{id}` sections plus `?edit=1` (sections, links to skills/topics), work items (add, status, delete), delete with a typed "delete" confirmation. `routers/projects.py` `FULL` now also returns `important_files_md`, `important_functions_md` and `revise_md`.
  - `pages_targets.py`: `/targets` (target upsert, pause, delete, live progress) and goals (create by metric, manual %, or roadmap project; update status; delete).
  - `pages_tracker.py`: `/tracker?day=` (clickable 12-month heatmap, day view, sessions add/edit/delete, daily log at `#log`).
  - `pages_insights.py`: `/analytics` (range chips, custom range, 10 charts), `/reports` (weekly/monthly with prev/next, CSV link, print button), `/settings` (backups create/restore/delete/download, JSON export/import, sessions CSV).
  - `charts.heatmap` gained `href=` and `selected=`.
- **Tested:** every module smoke-tested (GET + all POSTs, including validation errors), and a crawl of 900 internal links returned 200 everywhere.

## Remaining work (in order)
1. ~~Finish Learn~~ (done).
2. ~~Revision~~ (done).
3. ~~Practice~~ (done).
4. ~~Interview~~ (done).
5. ~~Track pages~~ (done).
6. ~~Tests~~ (done): `pytest -q` → 266 passed.
   - `test_seed_content.py` rewritten for v2: exercises solvable, topics/extras/curriculum/projects reference known slugs (v1 skill slugs go through `V1_SKILL_MAP`), every question well formed, and all 152 `output` questions re-run with `python -I`.
   - New `test_v2.py`: 1–6 scale, v1 quiz endpoints gone, grading per type, missed questions scheduled, mastery gate, targets, v2 exercise categories, every page returns 200.
   - `test_workflows.py`: exercise category `python` → `python-fundamentals`.
7. ~~Cleanup~~ (done):
   - `frontend/` (React v1) **moved, not deleted**, to `F:\Personal Projects\Skill_Development_v1_frontend_backup`.
   - `frontend_dist` and `PROJECT_DIR` removed from `config.py` and `tests/conftest.py`; `main.py` docstring updated.
   - `start.bat`: npm build step removed. `start-dev.bat`: now just uvicorn `--reload` (Vite removed).
   - `requirements.txt`: added `jinja2`, `markdown`, `pygments`.
   - README and `docs/02-architecture.md` rewritten for v2. Verified with a real uvicorn launch (`/`, `/learn/python`, `/static/app.css`, `/docs` → 200).
8. **Optional, expensive:** generate `backend/app/seed/content/projects_work.json`, documenting about 14 work services read-only (see `docs/content-spec.md`). The previous attempt hit the usage limit before writing anything.

## Gotchas found so far
- In Jinja, `dict.items` resolves to the method. Use `x["items"]` for a key named `items`, `keys` or `values`.
- Routes must be registered **before** `app.mount("/static", ...)` or the mount catches them (this was the `pygments.css` bug).
- Mapping v1 to v2 skill slugs can produce duplicate `extra_skills`; the loader dedupes them.
- The `backend/data/skilldev.db` from v1 has no user activity. Back it up or move it aside before the first v2 start, because the schema changed: `create_all` won't alter existing tables.
- Test against a throwaway data dir, never `backend/data`:
  `SKILLDEV_DATA_DIR=<scratchpad>/x backend/.venv/Scripts/python -c "...TestClient(create_app())..."`
- Running a test script from the scratchpad needs `PYTHONPATH=.` (run from `backend/`).
- httpx `TestClient.post(data=...)` needs a dict; repeated fields go as `{"qid": [1, 2]}`, not a list of tuples.
- Never pass a template variable named `range` (or any other Jinja global): it shadows `range()` and breaks `partials/quick_add.html`.
- Jinja has no lambdas: pass callables such as `heat_tip` from Python.
- Windows shell is Git Bash. The venv Python is `backend/.venv/Scripts/python`.

## Improvements pass (2026-11-24)
- **Python runner** (`services/runner.py`): per-test 2s timeout (rest skipped, whole-run backstop 20s), per-test ms and captured stdout, tracebacks limited to your own frames, friendly SyntaxError / RecursionError / "forgot return" messages. `run_python_script(code, stdin)` supports `input()`.
- **Pages**: `/playground` (stdin + Ctrl+Enter), "Run as script" button on exercises, `static/editor.js` (Tab indent, auto-indent, Ctrl+Enter) for every `textarea.code`.
- **Exercise editor**: `/practice/new`, `/practice/{id}/edit`; on save the solution is run against the tests and a warning is shown if it fails.
- **Migrations** (`app/migrate.py`): additive columns are applied at startup after a `pre-migrate` backup; `SCHEMA_VERSION` = 2; one-off data fixes go in `MIGRATIONS`. Dropping/renaming columns is NOT automated.
- **SRS ease**: `review_items.ease` (1.3–3.0, default 2.5) scales intervals beyond day 1 (`ease/2.5`); default behaviour is unchanged.
- **Weak-spot quiz**: `/revision/quiz?weak=1` ranks past misses (`assessment.weak_question_ids`).
- Project is now a git repo (no commits yet); `.gitignore` excludes `.venv/` and `data/`.
- Tests: 355 passing (`tests/test_improvements.py` is new).
