"""Loads reference content into the database.

Rules
- Only inserts (plus filling still-empty fields from late-arriving companion files); never
  overwrites anything you edited.
- Every inserted slug is recorded in `seed_registry`; an item you delete is never re-added.
- Seeds CONTENT only (and default targets). Never progress, attempts, sessions or proficiency.

Content layout (backend/app/seed/content):
  skills.json · curriculum.json · topics/*.json · topic_extras/*.json · exercises/*.json
  interview/*.json (roles.json uses `roles`) · interview/resume_claims.json
  projects.json (personal) · projects_work.json (work services)

Run manually:  python -m app.seed.loader
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Exercise,
    Flashcard,
    InterviewQuestion,
    Project,
    ProjectItem,
    Question,
    ResumeClaim,
    RoadmapItem,
    RoadmapStep,
    SeedRegistry,
    Skill,
    Target,
    Topic,
)

log = logging.getLogger("devgrowth.seed")

# v1 exercise categories -> v2 categories (by slug prefix / explicit slug)
EXERCISE_CATEGORY_REMAP = {
    "python": "python-fundamentals", "problem-solving": "dsa", "debugging": "advanced-python",
    "scenario": "backend", "backend": "backend",
}
EXERCISE_SLUG_CATEGORY = {
    "retry-decorator": "advanced-python", "lru-cache-sku-lookup": "advanced-python",
    "flatten-nested-json": "advanced-python", "merge-sorted-records-by-key": "advanced-python",
    "sql-upsert-inventory-snapshot": "postgresql", "sql-mark-returned-orders-update-from": "postgresql",
    "sql-category-revenue-rollup-recursive": "postgresql", "sql-find-duplicate-ingested-rows": "postgresql",
    "backend-orders-api-pagination": "api", "backend-idempotent-payment-webhook": "api",
}
AREA_OF_MODULE = {  # backend module number -> area
    **{n: "postgresql" for n in (1, 2, 3, 4, 5, 6, 7)}, **{n: "backend" for n in (8, 9, 10, 11, 12, 13, 16)},
    14: "aws", 15: "aws",
}
DEFAULT_TARGETS = [
    ("daily", "minutes", 60), ("daily", "reviews", 15),
    ("weekly", "minutes", 360), ("weekly", "exercises_solved", 5), ("weekly", "topics_passed", 2), ("weekly", "active_days", 5),
    ("monthly", "minutes", 1500), ("monthly", "topics_passed", 8), ("monthly", "exercises_solved", 20),
]
TOPIC_TEXT_FIELDS = ("summary", "details_md", "code_md", "use_cases_md", "mistakes_md", "best_practices_md",
                     "my_context_md", "why_md", "revision_notes_md", "exercises_md")


class _Ctx:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.registry = {(r.kind, r.slug) for r in db.scalars(select(SeedRegistry))}
        self.skills = {s.slug: s for s in db.scalars(select(Skill))}
        self.topics = {t.slug: t for t in db.scalars(select(Topic))}
        self.topic_track: dict[str, str] = {}
        self.report: dict[str, int] = {}
        self.warnings: list[str] = []

    def seen(self, kind: str, slug: str) -> bool:
        return (kind, slug) in self.registry

    def mark(self, kind: str, slug: str) -> None:
        self.registry.add((kind, slug))
        self.db.add(SeedRegistry(kind=kind, slug=slug))
        self.report[kind] = self.report.get(kind, 0) + 1

    def skill_id(self, slug: str | None) -> int | None:
        if not slug:
            return None
        s = self.skills.get(slug)
        if not s:
            self.warnings.append(f"unknown skill slug '{slug}'")
        return s.id if s else None


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _files(folder: Path):
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


# --------------------------------------------------------------------------- skills


def _seed_skills(ctx: _Ctx, content: Path) -> None:
    path = content / "skills.json"
    if not path.exists():
        return
    for raw in _read_json(path):
        slug = raw["slug"]
        if ctx.seen("skill", slug) or slug in ctx.skills:
            continue
        skill = Skill(
            slug=slug, name=raw["name"], category=raw.get("category", "Other"),
            description=raw.get("description", ""), classification=raw.get("classification", "to_learn"),
            evidence_notes=raw.get("evidence_notes", ""), track=raw.get("track", "none"),
            status=raw.get("status", "not_started"), next_action=raw.get("next_action", ""),
            proficiency=None,  # never guessed — you self-assess in the app
            source="seed",
        )
        ctx.db.add(skill)
        ctx.db.flush()
        ctx.skills[slug] = skill
        ctx.mark("skill", slug)


# --------------------------------------------------------------------------- curriculum


def _curriculum(content: Path) -> dict:
    path = content / "curriculum.json"
    return _read_json(path) if path.exists() else {"tracks": [], "projects": []}


def _topic_tracks(curr: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for track in curr.get("tracks", []):
        for mpos, module in enumerate(track["modules"], 1):
            for slug in module["topics"]:
                out.setdefault(slug, track["slug"])
    return out


# --------------------------------------------------------------------------- topics & questions


def _question_from(raw: dict, *, skill_id: int | None, position: int) -> Question | None:
    qtype = raw.get("type")
    if qtype == "mcq":
        opts, idx = raw.get("options", []), raw.get("answer_index")
        if len(opts) < 2 or idx is None or not 0 <= idx < len(opts):
            return None
    elif qtype in ("fill_blank", "output"):
        if not raw.get("answers"):
            return None
    elif qtype in ("debug", "explain", "qa"):
        if not (raw.get("answer") or raw.get("answer_md")):
            return None
    else:
        return None
    return Question(
        type=qtype, prompt=raw.get("prompt") or raw.get("question", ""), code=raw.get("code", ""),
        options=raw.get("options", []), answer_index=raw.get("answer_index"),
        answers=[str(a) for a in raw.get("answers", [])], answer_md=raw.get("answer") or raw.get("answer_md", ""),
        rubric=raw.get("rubric", []), explanation=raw.get("explanation", ""), skill_id=skill_id,
        position=position, source="seed",
    )


def _add_questions(topic: Topic, raws: list[dict], ctx: _Ctx) -> int:
    added = 0
    pos = len(topic.questions)
    for raw in raws:
        q = _question_from(raw, skill_id=topic.skill_id, position=pos)
        if q is None:
            ctx.warnings.append(f"{topic.slug}: skipped malformed question ({raw.get('type')})")
            continue
        topic.questions.append(q)
        pos += 1
        added += 1
    return added


def _seed_topics(ctx: _Ctx, content: Path) -> None:
    pending_links: list[tuple[Topic, list[str]]] = []
    for path in _files(content / "topics"):
        try:
            raw = _read_json(path)
        except json.JSONDecodeError as exc:
            ctx.warnings.append(f"{path.name}: invalid JSON ({exc})")
            continue
        slug = raw.get("slug") or path.stem
        if ctx.seen("topic", slug) or slug in ctx.topics:
            continue
        skill_id = ctx.skill_id(_map_skill(raw.get("skill")))
        topic = Topic(
            slug=slug, title=raw.get("title", slug), skill_id=skill_id, category=raw.get("category", "General"),
            difficulty=raw.get("difficulty", "intermediate"), tags=raw.get("tags", []),
            track=ctx.topic_track.get(slug, "none"), source="seed",
            **{f: raw.get(f, "") or "" for f in TOPIC_TEXT_FIELDS},
        )
        extra = {_map_skill(s) for s in raw.get("extra_skills", [])} - {_map_skill(raw.get("skill")), None}
        topic.extra_skills = [ctx.skills[m] for m in sorted(extra) if m in ctx.skills]
        for card in raw.get("flashcards", []):
            topic.flashcards.append(Flashcard(front=card["front"], back=card["back"], skill_id=skill_id, source="seed"))
        legacy_mcq = [{"type": "mcq", "prompt": q.get("question"), **q} for q in raw.get("quiz", [])]
        _add_questions(topic, legacy_mcq + raw.get("questions", []), ctx)
        ctx.db.add(topic)
        ctx.db.flush()
        for i, iq in enumerate(raw.get("interview", []), start=1):
            ctx.db.add(InterviewQuestion(
                slug=f"{slug}-iq{i}", topic_id=topic.id, skill_id=skill_id,
                category=_interview_category(topic.category), difficulty=iq.get("difficulty", "medium"),
                question=iq["question"], answer_md=iq.get("answer_md", ""), tags=raw.get("tags", [])[:5],
                roles=_roles_for_track(topic.track), source="seed",
            ))
        ctx.topics[slug] = topic
        ctx.mark("topic", slug)
        pending_links.append((topic, raw.get("related", [])))

    for topic, related in pending_links:
        for rslug in related:
            other = ctx.topics.get(rslug)
            if other and other is not topic and other not in topic.related:
                topic.related.append(other)


def _seed_topic_extras(ctx: _Ctx, content: Path) -> None:
    """Companion files: fill empty text fields, append new question types (once per topic)."""
    for path in _files(content / "topic_extras"):
        try:
            raw = _read_json(path)
        except json.JSONDecodeError as exc:
            ctx.warnings.append(f"{path.name}: invalid JSON ({exc})")
            continue
        slug = raw.get("slug") or path.stem
        topic = ctx.topics.get(slug)
        if topic is None or ctx.seen("topic_extras", slug):
            continue
        for f in ("why_md", "revision_notes_md", "exercises_md"):
            if raw.get(f) and not getattr(topic, f):
                setattr(topic, f, raw[f])
        # the companion my_context_md is written from the work-repo analysis -> prefer it while
        # the topic still has the seeded text (you haven't edited it)
        if raw.get("my_context_md") and topic.source == "seed":
            topic.my_context_md = raw["my_context_md"]
        _add_questions(topic, raw.get("questions", []), ctx)
        ctx.mark("topic_extras", slug)


def _interview_category(topic_category: str) -> str:
    return {"Python": "python", "Database": "sql", "Backend": "backend", "Cloud & DevOps": "cloud",
            "Testing": "python", "Tools": "backend", "AI": "ai", "Computer Science": "dsa"}.get(topic_category, "backend")


def _roles_for_track(track: str) -> list[str]:
    return {"python": ["python-dev", "python-backend"], "backend": ["python-backend", "backend-engineer"],
            "ai": ["ai-engineer", "genai-engineer"]}.get(track, [])


# --------------------------------------------------------------------------- exercises & interview


def _seed_exercises(ctx: _Ctx, content: Path) -> None:
    for path in _files(content / "exercises"):
        try:
            raw = _read_json(path)
        except json.JSONDecodeError as exc:
            ctx.warnings.append(f"{path.name}: invalid JSON ({exc})")
            continue
        slug = raw.get("slug") or path.stem
        if raw.get("language") == "javascript" or ctx.seen("exercise", slug):
            continue
        if ctx.db.scalar(select(Exercise.id).where(Exercise.slug == slug)):
            continue
        category = EXERCISE_SLUG_CATEGORY.get(slug) or EXERCISE_CATEGORY_REMAP.get(raw.get("category", ""), raw.get("category", "python-fundamentals"))
        ex = Exercise(
            slug=slug, title=raw["title"], category=category, language=raw.get("language", "python"),
            difficulty=raw.get("difficulty", "medium"), skill_id=ctx.skill_id(_map_skill(raw.get("skill"))),
            concepts=raw.get("concepts", []), problem_md=raw.get("problem_md", ""), examples=raw.get("examples", []),
            hints=raw.get("hints", []), starter_code=raw.get("starter_code", ""), solution_code=raw.get("solution_code", ""),
            solution_md=raw.get("solution_md", ""), tests=raw.get("tests"), source="seed",
        )
        ex.topics = [ctx.topics[t] for t in raw.get("related_topics", []) if t in ctx.topics]
        ctx.db.add(ex)
        ctx.mark("exercise", slug)


# v1 skill slugs used by older content files -> v2 skills
V1_SKILL_MAP = {
    "sql": "postgresql", "sql-joins": "postgresql", "sql-aggregations": "postgresql", "sql-subqueries": "postgresql",
    "sql-ctes": "postgresql", "sql-indexing": "query-optimization", "transactions-concurrency": "transactions-locking",
    "rest-api-integration": "api-integration", "api-development": "api-design", "testing": "python-testing",
    "async-concurrency": "python-asyncio", "webhooks": "api-integration", "javascript": None,
    "pandas": "etl-data-processing", "python-venv": "python-packaging", "security": "web-security",
    "logging-observability": "python-exceptions-logging", "react": None,
}


def _map_skill(slug: str | None) -> str | None:
    if slug in V1_SKILL_MAP:
        return V1_SKILL_MAP[slug]
    return slug


def _seed_interview(ctx: _Ctx, content: Path) -> None:
    for path in _files(content / "interview"):
        if path.name == "resume_claims.json":
            continue
        try:
            items = _read_json(path)
        except json.JSONDecodeError as exc:
            ctx.warnings.append(f"{path.name}: invalid JSON ({exc})")
            continue
        for raw in items:
            slug = raw.get("slug")
            if not slug or ctx.seen("interview", slug):
                continue
            if ctx.db.scalar(select(InterviewQuestion.id).where(InterviewQuestion.slug == slug)):
                continue
            category = raw.get("category", "backend")
            roles = raw.get("roles") or {
                "dsa": ["python-dev", "python-backend"], "system-design": ["backend-engineer", "python-backend"],
                "scenario": ["python-backend", "backend-engineer"],
            }.get(category, [])
            ctx.db.add(InterviewQuestion(
                slug=slug, skill_id=ctx.skill_id(_map_skill(raw.get("skill"))), category=category,
                difficulty=raw.get("difficulty", "medium"), question=raw["question"],
                answer_md=raw.get("answer_md", ""), tags=raw.get("tags", []), roles=roles,
                project_name=raw.get("project") or "", source="seed",
            ))
            ctx.mark("interview", slug)


def _seed_claims(ctx: _Ctx, content: Path) -> None:
    path = content / "interview" / "resume_claims.json"
    if not path.exists():
        return
    for raw in _read_json(path):
        slug = raw.get("slug")
        if not slug or ctx.seen("claim", slug):
            continue
        ctx.db.add(ResumeClaim(
            slug=slug, claim=raw["claim"], evidence_md=raw.get("evidence_md", ""),
            status=raw.get("status", "needs-preparation"), likely_questions=raw.get("likely_questions", []),
            star_prompts=raw.get("star_prompts", {}), source="seed",
        ))
        ctx.mark("claim", slug)


# --------------------------------------------------------------------------- projects & roadmap


PROJECT_TEXT_FIELDS = (
    "summary", "purpose_md", "architecture_md", "folder_structure_md", "modules_md", "database_md", "apis_md",
    "business_logic_md", "challenges_md", "solutions_md", "contributions_md", "learnings_md",
    "pending_improvements_md", "important_files_md", "important_functions_md", "revise_md",
    "source_path", "evidence_note", "organization", "role",
)


def _seed_projects(ctx: _Ctx, content: Path) -> None:
    for name in ("projects_work.json", "projects.json"):
        path = content / name
        if not path.exists():
            continue
        try:
            entries = _read_json(path)
        except json.JSONDecodeError as exc:
            ctx.warnings.append(f"{name}: invalid JSON ({exc})")
            continue
        for raw in entries:
            slug = raw["slug"]
            if ctx.seen("project", slug) or ctx.db.scalar(select(Project.id).where(Project.slug == slug)):
                continue
            project = Project(
                slug=slug, name=raw["name"], kind=raw.get("kind", "personal"), status=raw.get("status", "unknown"),
                start_date=_date(raw.get("start_date")), end_date=_date(raw.get("end_date")),
                tech=raw.get("tech", []), source="seed",
                **{f: raw.get(f, "") or "" for f in PROJECT_TEXT_FIELDS},
            )
            project.skills = [ctx.skills[s] for s in raw.get("skills", []) if s in ctx.skills]
            project.topics = [ctx.topics[t] for t in raw.get("topics", []) if t in ctx.topics]
            for item in raw.get("items", []):
                project.items.append(ProjectItem(kind=item["kind"], title=item["title"],
                                                 description=item.get("description", ""), status=item.get("status", "todo")))
            ctx.db.add(project)
            ctx.db.flush()
            main_skill = project.skills[0].id if project.skills else None
            for i, iq in enumerate(raw.get("interview", []), start=1):
                ctx.db.add(InterviewQuestion(
                    slug=f"{slug}-iq{i}", project_id=project.id, skill_id=main_skill, category="project",
                    difficulty=iq.get("d", "medium"), question=iq["q"], roles=["python-backend", "backend-engineer"],
                    project_name=project.name,
                    answer_md=("_Project question — answer from the project page (architecture, challenges, solutions). "
                               "Structure: context → what you did → why → result → what you'd improve._"),
                    source="seed",
                ))
            ctx.mark("project", slug)


def _seed_curriculum(ctx: _Ctx, curr: dict) -> None:
    order = 0
    for track in curr.get("tracks", []):
        for mpos, module in enumerate(track["modules"], 1):
            order += 1
            slug = f"{track['slug']}-{module['n']:02d}"
            if ctx.seen("roadmap", slug) or ctx.db.scalar(select(RoadmapItem.id).where(RoadmapItem.slug == slug)):
                continue
            area = track["slug"] if track["slug"] in ("python", "ai") else AREA_OF_MODULE.get(module["n"], "backend")
            item = RoadmapItem(
                slug=slug, title=module["title"], track=track["slug"], area=area, module_no=mpos,
                label=module.get("label", "new"), sort_order=order, level="intermediate", source="seed",
                why_md=module.get("why_md", ""),
            )
            for i, tslug in enumerate(module["topics"]):
                topic = ctx.topics.get(tslug)
                item.steps.append(RoadmapStep(title=topic.title if topic else tslug,
                                              topic_id=topic.id if topic else None, sort_order=i))
            ctx.db.add(item)
            ctx.mark("roadmap", slug)
    for proj in curr.get("projects", []):
        slug = proj["slug"]
        if ctx.seen("roadmap", slug) or ctx.db.scalar(select(RoadmapItem.id).where(RoadmapItem.slug == slug)):
            continue
        order += 1
        item = RoadmapItem(slug=slug, title=proj["title"], track="projects", area=None, label="new",
                           level=proj.get("level", "intermediate"), sort_order=order, why_md=proj.get("why_md", ""),
                           source="seed")
        for i, step in enumerate(proj.get("steps", [])):
            item.steps.append(RoadmapStep(title=step, sort_order=i))
        ctx.db.add(item)
        ctx.mark("roadmap", slug)


def _relink_steps(ctx: _Ctx) -> None:
    """Steps created before their topic file existed are stored with the slug as title."""
    for step in ctx.db.scalars(select(RoadmapStep).where(RoadmapStep.topic_id.is_(None))):
        topic = ctx.topics.get(step.title)
        if topic:
            step.topic_id = topic.id
            step.title = topic.title


def _relink_topic_tracks(ctx: _Ctx) -> None:
    for slug, track in ctx.topic_track.items():
        t = ctx.topics.get(slug)
        if t and t.track in (None, "", "none"):
            t.track = track


def _seed_targets(ctx: _Ctx) -> None:
    if ctx.seen("targets", "defaults"):
        return
    for period, metric, value in DEFAULT_TARGETS:
        if not ctx.db.scalar(select(Target.id).where(Target.period == period, Target.metric == metric)):
            ctx.db.add(Target(period=period, metric=metric, value=value))
    ctx.mark("targets", "defaults")


def seed_all(db: Session, content_dir: Path) -> dict[str, Any]:
    """Insert any seed content not seen before. Returns counts per kind + warnings."""
    ctx = _Ctx(db)
    curr = _curriculum(content_dir)
    ctx.topic_track = _topic_tracks(curr)
    _seed_skills(ctx, content_dir)
    _seed_topics(ctx, content_dir)
    _seed_topic_extras(ctx, content_dir)
    _relink_topic_tracks(ctx)
    _seed_exercises(ctx, content_dir)
    _seed_interview(ctx, content_dir)
    _seed_claims(ctx, content_dir)
    _seed_projects(ctx, content_dir)
    _seed_curriculum(ctx, curr)
    db.flush()
    _relink_steps(ctx)
    _seed_targets(ctx)
    db.commit()
    for w in ctx.warnings:
        log.warning("seed: %s", w)
    return {"inserted": ctx.report, "warnings": ctx.warnings}


if __name__ == "__main__":  # pragma: no cover
    from ..config import load_settings
    from ..db import SessionLocal, init_engine
    from ..main import create_schema

    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_engine(settings.database_url)
    create_schema()
    with SessionLocal() as session:
        print(seed_all(session, settings.seed_dir))
