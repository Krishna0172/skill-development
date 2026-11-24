# Phase 1 — Knowledge Assessment

_Assessment date: 2026-11-18. Read-only analysis — no existing files were modified._

## 1. What was analysed

| Source | Location | What it is | Evidence strength |
|---|---|---|---|
| Resume (Nov 2026) | `KrishnaTechnicals/Krishna_Resume_2026.pdf` + `.html` | Current resume | Self-reported (professional) |
| Resume (Jul 2026) | `Learning My Forcesight code/Krishna Resume.pdf` (identical copy in `KrishnaTechnicals/`) | Older resume version | Self-reported (professional) |
| Work study guide | `Learning My Forcesight code/LEARNING_GUIDE.md` | Detailed write-up of the ForceSight `ECSMarketplaceDataUpload` service (file names, tables, SQL patterns) | Second-hand description of code — **the actual ForceSight source code is not in this workspace** |
| InstaPostDownloader | `Personal Projects/InstaPostDownloader` | FastAPI + instaloader web app (~600 lines Python + 358-line HTML UI) | Source code |
| YoutubePlaylistDownloader | `Personal Projects/YoutubePlaylistDownloader` | FastAPI + yt-dlp web app (~700 lines Python + 429-line HTML UI) | Source code |
| Youtube Playlist downloader | `Personal Projects/Youtube Playlist downloader` | Earlier CLI version (argparse + yt-dlp) | Source code |
| Iphone to Android Media convert | `Personal Projects/Iphone to Android Media convert` | CLI: HEIC→JPEG (Pillow), MOV→MP4 (ffmpeg subprocess) | Source code |
| Jpg image conversion | `Personal Projects/Jpg image conversion` | 64-line Pillow script | Source code |
| Bol Hindi | `Personal Projects/Hindi learning` | Static vanilla-JS PWA (~4,800 lines JS/HTML/CSS), service worker, Leitner spaced repetition | Source code |
| Thoughts For Life | `Personal Projects/Thoughts For Life` | React 19 + TypeScript + Vite (~1,250 lines), localStorage, jsPDF export | Source code |
| Scratch to Pro | `Personal Projects/English Learning Sctrach to Pro` | Next.js 16 App Router + React 19 + TS + Tailwind 4 + OpenAI-compatible API + zod (~5,800 lines TS/TSX) | Source code, **built with Claude Code** (`.claude/` folder present) |

Not analysed: `Videos/` (media only), `.env` files (secrets, intentionally skipped).

### Important caveats

1. **ForceSight work can't be checked against code.** Everything about the professional work comes from the resume and `LEARNING_GUIDE.md`, which is itself a generated study guide. I treated it as credible but self-reported.
2. **The personal projects show signs of AI-assisted development.** Scratch to Pro has a `.claude/` folder and `AGENTS.md`. The other recent projects (Aug–Nov 2026) have the same polished style, but I can't tell how much of the code you wrote by hand. Because of that, **I didn't set any proficiency levels.** I recorded *evidence* separately, and you set your own proficiency in the app.
3. **Git usage in personal projects is minimal.** Only 1 of the 9 personal folders is a git repository, and it has a single commit. The resume's "1,000+ commits" refers to work repositories.

### Inconsistencies to fix before interviews

| Claim | Resume (Nov 2026) | LEARNING_GUIDE.md | Older resume |
|---|---|---|---|
| Channels | 20+ sales channels | 30+ channels | 20+ |
| Processors | 170+ report types, 150+ processors written personally | 150+ specialized processors | — |
| Experience | 2.5+ years full-time | — | "nearly 2 years" |
| Runtime | Containerized service on **ECS** | "runs on AWS Lambda/ECS", entry point is `lambda_handler` | Lambda + S3 |

Make sure the numbers you say out loud match your resume. The Lambda-vs-ECS story ("a Lambda-style handler packaged in a container and run on ECS") is a good interview question in itself. The app includes it.

---

## 2. Skill inventory

Evidence levels used throughout the app:

- **Professional** — described in your resume or work guide (self-reported, used at work)
- **Personal code** — visible in your personal project source code
- **AI-assisted** — present in personal code that shows AI-tool markers; depth unknown
- **Resume only** — listed with no detail anywhere
- **Not found** — no evidence anywhere → a learning gap

### Programming languages
| Skill | Evidence | Notes |
|---|---|---|
| Python | Professional + Personal code | Primary language. Personal code uses type hints, `dataclasses`, `pathlib`, `argparse`, `subprocess`, custom exceptions, `threading.Event`, `asyncio.to_thread` |
| SQL | Professional | Raw SQL via psycopg2; upserts with `ON CONFLICT`, `COALESCE(EXCLUDED.col, t.col)` |
| JavaScript | Personal code | Vanilla JS PWA: DOM rendering, events, localStorage, service worker, Web Speech API, IIFE modules |
| TypeScript | Personal code (AI-assisted) | Typed React/Next apps, discriminated unions in Scratch to Pro |
| HTML & CSS | Personal code | Static sites + single-file UIs for the FastAPI apps |

### Backend development
| Skill | Evidence | Notes |
|---|---|---|
| REST API integration (consuming) | Professional | Unicommerce, EasyEcom, OmsGuru, PayU, Easebuzz, Shiprocket, Shippigo (resume) |
| Building REST APIs | Personal code + Professional (resume: "returns recall API") | FastAPI endpoints with Pydantic models, `HTTPException`, background jobs, SSE progress streams |
| FastAPI | Personal code | 2 apps. No dependency injection, auth, DB or tests used |
| Pydantic / validation | Personal code + resume | `BaseModel`, `Field(ge=, le=)`; zod in Next.js |
| Backend architecture | Professional | Registry/dispatcher with lazy imports; central `DatabaseManager`; standard processor pipeline |
| Error handling & resilience | Professional + Personal code | Deadlock retry; custom exception hierarchy mapped to HTTP 400; per-file failure isolation; model fallback on 429/503 |
| Async & concurrency | Personal code | `asyncio.to_thread`, `run_in_executor`, `threading.Event` cancellation, SSE polling loop |
| ETL & data processing | Professional | pandas pipelines over CSV/XLSX/XLS/XLSB/ZIP/PDF with header detection and case-insensitive column matching |
| pandas / NumPy | Professional | `read_csv`/`read_excel`, rename, merge, groupby, `np.where` |
| Webhooks | Resume only | Listed under Backend; no detail |
| **Authentication** | **Not found** | No login, JWT, OAuth or password hashing in any project |
| **Automated testing** | **Not found** | No test files in any project. `lambda_test.py` is a manual harness, not a test suite |

### Frontend development
| Skill | Evidence | Notes |
|---|---|---|
| React | Personal code (AI-assisted) | Hooks, custom `useAppData` hook, lifted state |
| Next.js | Personal code (AI-assisted) | App Router, route handlers, dynamic segments |
| Tailwind CSS | Personal code (AI-assisted) | Tailwind v4 in Scratch to Pro |
| Browser APIs / PWA | Personal code | Service worker caching, manifest, Speech Synthesis |

### Database
| Skill | Evidence | Notes |
|---|---|---|
| PostgreSQL | Professional | Citus (distributed Postgres), bulk upserts, transactions |
| psycopg2 | Professional | `execute_batch`, connection pooling, transactions |
| Upserts / transactions / deadlocks | Professional | `ON CONFLICT DO NOTHING` vs `DO UPDATE`, retry on `DeadlockDetected` |
| Joins, aggregations | Inferred from professional work | Almost certainly used, but no queries were available to check |
| Subqueries, CTEs, window functions | **Unverified** | No evidence either way. The app's SQL challenges act as a diagnostic |
| Indexing, query optimization | Resume claim | "Reduced reconciliation time by 40%". You need to be able to explain *how* (EXPLAIN plans, indexes, batching) |
| Database design | Inferred | Upload-tracking and order/inventory tables; normalization depth unknown |
| DynamoDB | Professional | Sync-request state table |
| **SQLAlchemy / ORM / migrations** | **Not found** | The guide says explicitly: "You do not use SQLAlchemy" |

### Cloud & DevOps
| Skill | Evidence | Notes |
|---|---|---|
| AWS Lambda (+ Layers) | Professional + Coursera certificate | Async invoke, cold-start-aware lazy imports |
| S3 | Professional | Presigned URL downloads |
| ECS / Fargate | Professional | Containerized service, `run_task` |
| API Gateway | Professional (guide) | Event shape with `requestContext.authorizer` |
| Boto3 | Professional | Lambda, S3, ECS, DynamoDB clients |
| CloudWatch | Resume only | Monitoring mentioned |
| Pulumi (IaC) | Professional (resume) | No detail on stacks/language |
| Docker | Professional (resume + guide's Dockerfile) | Not used in any personal project |
| **CI/CD** | **Not found** | No pipelines anywhere |

### Development tools
| Skill | Evidence |
|---|---|
| Git / GitHub | Professional (1,000+ commits per resume). Barely used on personal projects |
| VS Code | In use (this environment) |
| Cursor AI | Resume |
| Claude Code | Personal code (`.claude/` in Scratch to Pro) |
| Python venv/pip | Personal code (`requirements.txt` + `run.bat` per project) |
| Postman | Resume |
| Node.js / npm / Vite | Personal code |

### Other
| Skill | Evidence |
|---|---|
| Data structures & algorithms | Resume claim: 1,500+ problems (LeetCode/CodeChef/SkillRack) |
| LLM API integration | Personal code — OpenAI-compatible client with model fallback, JSON-mode responses, zod-validated output |
| Media processing (Pillow, ffmpeg) | Personal code |

---

## 3. Learning gaps (priority order)

These are ranked by how much they would help a backend developer with 2.5 years' experience in interviews and on the job:

1. **Automated testing (pytest)** — zero evidence across 9 projects; expected at every level.
2. **Authentication & authorization** — JWT, OAuth2 flows, password hashing, sessions vs tokens.
3. **SQL depth verification** — window functions, CTEs, EXPLAIN ANALYZE and index design. You need to be able to back up the 40% optimization claim with concrete technique.
4. **SQLAlchemy 2.0 + Alembic migrations** — the ORM and schema-migration workflow used in most Python shops.
5. **API design** — pagination, idempotency keys, versioning, status-code discipline, OpenAPI.
6. **CI/CD (GitHub Actions)** and **Docker in your own projects**.
7. **Caching (Redis) and message queues (SQS/Celery)** — the usual next step after cron/Lambda pipelines.
8. **System design fundamentals** — you've built a real ingestion system; turn that into structured design answers.
9. **Python depth** — decorators, generators, context managers, async internals, typing.
10. **Security basics** — OWASP Top 10, secrets management, SQL injection (psycopg2 parameterization).

### What I deliberately did NOT recommend
- Beginner Python, basic SQL SELECTs, or "what is an API" content. You have 2.5 years of professional work in these.
- AWS fundamentals for Lambda/S3/ECS. The roadmap goes straight to deeper material (concurrency limits, observability, cost).

## 4. Missing information (only you can fill these in)
- Your **self-assessed proficiency** for every skill (the app asks for it and keeps a history).
- Details of the **20+ other ForceSight services** (integrations, reconciliation, returns recall API): architecture, tables, challenges.
- **How the 40% optimization was achieved.**
- Whether the personal projects are finished, paused or abandoned (status starts as "Unknown").
- How much of each personal project you wrote yourself vs. generated with AI.
