# DevGrowth — Personal Developer Growth Platform

A local, private learning platform for a Python backend developer moving towards AI Engineering. It combines a knowledge base with assessments, spaced-repetition revision, coding practice, interview prep and a progress tracker, all built around your own resume and project history.

It answers three questions every day:

1. **What do I actually know?** Topics count as learned only after you pass their assessment. Skills carry evidence and a self-rated 1–6 proficiency.
2. **What am I forgetting?** The revision queue, missed questions, weak concepts and topics that need revision.
3. **What should I learn next?** Three learning tracks (Python, Backend, AI), targets, goals and today's plan.

Everything runs on your machine (`127.0.0.1`) and is stored in one SQLite file. Nothing is sent anywhere.

---

## Quick start (Windows)

**Requirement:** Python 3.12+. Node.js is not needed.

```
double-click start.bat
```

The first run creates `backend\.venv` and installs the dependencies, then opens **http://127.0.0.1:8765**. Later runs start in a few seconds. Press `Ctrl+C` in the window to stop.

### Manual start

```bat
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

### Development mode

Run `start-dev.bat`. The server restarts when Python files change; template changes show up on the next page load.

### Run the tests

```bat
cd backend
.venv\Scripts\python -m pytest -q
```

There are 266 tests. They cover:
- the spaced-repetition ladder and overlap-safe time maths;
- grading and the mastery gate;
- end-to-end workflows, backup/restore and import;
- every page returning 200;
- the seeded content: every exercise's reference solution is run through the real code runner, and every "predict the output" question is re-run to confirm its answer.

---

## First-time checklist

1. **Skills → Self-assess.** Rate each skill 1–6 and set a target. The app ships with *evidence* from the workspace analysis but **never guesses your level**.
2. **Learn → Python / Backend / AI.** Work through the modules. On each topic, read it, then press **Assess**. Passing (≥ 70%) marks the topic *learned* and starts its revision cycle.
3. **Targets & goals.** Default daily/weekly targets are set up; adjust them. Add a goal, e.g. *exercises solved = 30*.
4. **Interview → Resume claims.** Write a STAR story for each claim and mark it *ready* once you've rehearsed it.
5. **Projects.** Fill in the sections for the projects you've built, especially *Learnings* and *What I need to revise*.

## Daily workflow

| When | Where | What |
|---|---|---|
| Morning | **Today** | What to study, revisions due, targets, yesterday's summary |
| While studying | **▶ Timer** (top bar) or **Study** on a topic | Time is attributed to the topic and its skill. Only one timer runs at a time |
| | **Revision → Start session** | One item at a time: recall, reveal, rate (Forgot / Difficult / Need practice / Easy). Questions are graded; a wrong answer counts as *forgot* |
| | **Practice** | *Run tests* checks without recording; *Submit* records an attempt. The solution unlocks after your first attempt |
| Evening | **Tracker → End-of-day log** | What you studied, accomplished, got stuck on, and tomorrow's plan |
| Weekly | **Reports** | Weekly summary and next week's priorities → *Print* or *CSV* |

Press `/` anywhere to search.

---

## What's inside

| Section | Highlights |
|---|---|
| **Today / Dashboard** | Today's plan, area progress, role readiness, targets, streak and charts |
| **Learn** | Three tracks (Python Mastery, Backend Engineering, AI Engineering) with 45 modules and 4 build projects |
| **Knowledge base** | 108 topics: why it matters, explanation, code, mistakes, best practices, *where this shows up in your work*, revision notes, exercises, your notes, flashcards. Markdown with syntax highlighting |
| **Assessments** | 937 typed questions: multiple choice, fill in the blank, predict the output, debugging, explain, Q&A. Auto-graded or self-graded against a rubric. Pass at 70%; mastery needs ≥ 85% plus spaced revisions |
| **Revision** | Spaced repetition over topics, flashcards, questions and interview questions; a quick mixed quiz; frequently forgotten and consistently remembered lists |
| **Practice** | 72 exercises (65 auto-checked in Python and SQL), grouped by category. Hints, run, submit, give up; the solution stays locked until you've attempted |
| **Interview** | 371 questions filtered by role (Python Developer, Backend Engineer, AI Engineer, …); mock interviews; 14 resume claims with STAR stories |
| **Skills** | 54 skills with an evidence classification, 1–6 proficiency and target, history of ratings, linked topics and exercises |
| **Projects** | Documented builds with 15 sections, work items, linked skills/topics and time logged |
| **Targets & goals** | Daily/weekly/monthly targets and metric-driven goals, all computed from your records |
| **Tracker** | 12-month calendar (click a day), day view, sessions add/edit/delete, end-of-day log |
| **Analytics** | 10 charts over 7 days to all time or a custom range |
| **Reports** | Weekly and monthly, with comparison to the previous period; CSV and print |
| **Settings** | Backups (automatic daily, plus manual), restore, download, JSON export/import |

---

## Your data

- **Database:** `backend\data\skilldev.db` (SQLite).
- **Automatic backups:** `backend\data\backups\`. One is taken per day on start, plus a safety backup before every restore or import.
- **Portable export:** *Settings → Export everything (JSON)* writes every table to one file. Copy it somewhere off this machine now and then.
- **Browser storage** holds only the theme cookie. No learning data is kept in the browser.
- **Reference content** (topics, questions, exercises, curriculum, interview questions, projects) lives in `backend\app\seed\content\`. It is inserted once and never overwrites your edits; items you delete stay deleted.

### Using PostgreSQL instead of SQLite (optional)

```bat
set SKILLDEV_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/devgrowth
```

Install `psycopg2-binary` into the venv first. File backups are SQLite-only, so use JSON export with PostgreSQL.

---

## Architecture

See [`docs/02-architecture.md`](docs/02-architecture.md) for the layers, schema, data rules, revision and mastery logic, and how the sections connect. The assessment the v2 plan is based on is in [`docs/03-work-repo-assessment.md`](docs/03-work-repo-assessment.md), and the content format is in [`docs/content-spec.md`](docs/content-spec.md).

```
Skill_Development/
├── start.bat / start-dev.bat
├── docs/                 assessment, architecture, content spec, handoff
└── backend/              FastAPI + SQLAlchemy + SQLite + Jinja2/HTMX
    ├── app/
    │   ├── web/          page routes (pages_*.py), template helpers, SVG charts
    │   ├── templates/    Jinja2 pages, macros, partials
    │   ├── static/       app.css, htmx.min.js
    │   ├── routers/      JSON API under /api
    │   ├── services/     domain logic (revision, assessment, runner, stats, …)
    │   └── seed/         reference content (JSON) + loader
    ├── tests/            pytest suite
    └── data/             your database + backups (created on first run)
```

## Troubleshooting

| Problem | Fix |
|---|---|
| The page doesn't load | The server window was closed. Run `start.bat` again |
| Port 8765 in use | Change the port in `start.bat` (both the `start` URL and `--port`) |
| Errors after upgrading from v1 | The v1 database has an older schema. Stop the app, move `backend\data\skilldev.db` somewhere safe and restart |
| Want a clean slate | Stop the app, move `backend\data\skilldev.db` somewhere safe and restart |
