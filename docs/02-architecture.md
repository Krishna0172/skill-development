# Architecture (v2)

## Choices and why

| Concern | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (Python 3.12+) | You already use FastAPI, so the code is readable to you. Pydantic validates every request. |
| ORM / DB | **SQLAlchemy 2.0** + **SQLite** | Zero setup on Windows, one file to back up. Models use portable types only, so you can switch to PostgreSQL with `SKILLDEV_DATABASE_URL`. |
| Pages | **Jinja2 templates + a little HTMX** | Python-only: no build step, no Node, no JavaScript framework to maintain. HTMX handles the few partial updates (e.g. running exercise tests). |
| Styling | One hand-written `app.css` with CSS-variable tokens | Dark (default) and light themes from one set of variables; the choice is a cookie. |
| Charts | Server-rendered **SVG** (`web/charts.py`) | Bars, lines, horizontal bars, part-to-whole strips, a calendar heatmap and rings. A colour-blind-safe palette and no JavaScript. |
| Markdown | `markdown` + **Pygments** | Notes and content render server-side with syntax highlighting. |
| Tests | **pytest** + FastAPI TestClient | 266 tests: engines, workflows, data safety, pages, and the seeded content itself. |

The app is single-user and **local-only**. It binds to `127.0.0.1` and has no login.

## Layers

```
backend/app/
  web/          server-rendered pages
    core.py       templates, filters (md, date, rel, minutes, hours, label), globals (badge, progress, dots,
                  code_block, charts), render(), redirect(url, msg, err), form_int
    charts.py     SVG charts
    pages_*.py    one module per area: daily, learn, revision, practice, interview, skills, projects,
                  targets, tracker, insights (analytics, reports, settings)
  templates/    base.html, macros.html (card, stat, kv, empty, chips, question_form), partials/, one file per page
  routers/      JSON API under /api: validation and response shaping
  services/     domain logic (no HTTP):
    assessment.py  grading per question type, attempts, pass (70%) and mastery (85%) thresholds
    topics.py      topic status transitions and the evidence check
    revision.py    spaced-repetition engine (pure next_state + persistence)
    progress.py    area progress, role readiness, targets
    timecalc.py    overlap-safe time accounting (interval union)
    runner.py      runs practice code (Python subprocess, SQL in-memory)
    stats.py       dashboard, analytics, day summaries
    goals.py, reports.py, search.py, backup.py
  models.py     SQLAlchemy models (single source of truth)
  schemas.py    Pydantic request models
  history.py    append-only history journal
  seed/         reference content (JSON) + idempotent loader
```

**Pages call the API functions directly.** A page route such as `POST /topics/{id}/status` calls `routers.knowledge.update_topic(...)` as an ordinary Python function. The validation and side effects therefore live in one place, shared by the JSON API and the pages. Page routes are registered before the `/static` mount, or the mount would catch them.

## Database schema (main tables)

```
skills ──< topics ──< flashcards                  review_items (item_type, item_id) ──< review_logs
   │         │  ├──< questions (typed)                  ▲ topic / flashcard / question / interview
   │         │  ├──< interview_questions
   │         │  ├── topic_links (related)          exercises ──< exercise_attempts
   │         │  └── topic_extra_skills             exercises >──< topics (exercise_topics)
   │         └──< assessment_attempts (assessment / quiz / mock_interview)
   │
   ├──< learning_sessions (time; timer or manual)   daily_logs (one per date)
   ├──< goals (metric-driven progress)               targets (daily / weekly / monthly)
   ├──< roadmap_items (area, module_no, label) ──< roadmap_steps (→ topic)
   └──< projects >──< skills / topics; projects ──< project_items
resume_claims (STAR) · history_events (append-only journal)
app_meta (tracking start, schema version) · seed_registry (seeded slugs, never re-added)
```

### Rules that keep the data trustworthy

- **Reading isn't learning.** A topic with questions can become *learned* only by passing its assessment (≥ 70%). *Mastered* needs ≥ 85% **and** successful revisions up to the 14-day interval. A manual status change goes through the same evidence check, and revision ratings can't promote a topic without it.
- **Never overwrite history.** Status and proficiency changes are written to `history_events`, revision answers to `review_logs`, and every assessment is kept with its per-question details.
- **No stored counters.** Hours, streaks, completion percentages, target and goal progress are computed from raw rows on every request.
- **No double-counted time.** Sessions are split at midnight and merged with an interval union before summing. Only one timer can run at a time.
- **Idempotent writes.** Reviews, attempts, assessments and manual sessions accept a `client_ref`, so a double submit can't create duplicates.
- **Proficiency is never guessed.** Seed skills carry *evidence* (a classification); `proficiency` (1–6) stays empty until you rate it.
- **Seeding only inserts.** Your edits are never overwritten, and items you delete stay deleted.

## Assessments

| Type | Graded by |
|---|---|
| Multiple choice | Option index |
| Fill in the blank | Any accepted answer, case- and whitespace-insensitive |
| Predict the output | Exact output, ignoring trailing whitespace (every seeded answer is verified by running the code) |
| Debugging / Explain / Q&A | Self-graded: reveal the model answer and tick the rubric points you covered |

Missed auto-graded questions are scheduled into revision as `question` items.

## Spaced repetition

Ladder: **1 → 3 → 7 → 14 → 30 → 60 → 120 days**. A topic enters the ladder when it's learned.

| Rating | Effect |
|---|---|
| Easy | Up one rung |
| Need practice | Same rung again |
| Difficult | Down one rung |
| Forgot | Back to Day 1, lapse +1; topic → *needs revision* |

In a revision session, `question` items are graded: wrong → *forgot*, partially self-graded → *difficult*, right → you choose *easy* or *need practice*.

## How sections connect

| When you… | …this happens automatically |
|---|---|
| Pass a topic's assessment | Topic → *learned*, revision scheduled (Day 1), linked curriculum step counts as done, history logged |
| Fail it after learning | Topic → *needs revision* |
| Rate a revision | Next due date set, topic status updated, the skill's recall stats change |
| Solve an exercise (first time) | Linked topics move *not started/learning → practised*; goals using "exercises solved" advance |
| Start or stop a timer | Time counts towards the topic, its skill, targets, daily/weekly/monthly totals and the streak |
| Rate a skill | Recorded in its history; drawn on the skill page; listed in reports under "Skills improved" |
| Finish a mock interview | Each answer's confidence is recorded; answers rated 1–2 go into revision |

## API

The JSON API is under `/api`. Interactive docs are at `http://127.0.0.1:8765/docs` while the app is running.
