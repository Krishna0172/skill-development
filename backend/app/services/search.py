"""Global search across every content type.

Uses case-insensitive LIKE matching on the main text fields of each table, then ranks results
in Python (title match > tag/summary match > body match). For one person's knowledge base
(thousands of rows) this stays well under ~50 ms and works the same on SQLite and PostgreSQL.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Exercise, Flashcard, InterviewQuestion, Project, RoadmapItem, Skill, Topic

TYPES = ("skill", "topic", "exercise", "interview", "project", "roadmap", "flashcard")


def _snippet(text: str, terms: list[str], width: int = 160) -> str:
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text)
    low = flat.lower()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=-1)
    if pos < 0:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _score(title: str, mid: str, body: str, terms: list[str], phrase: str) -> int:
    t, m, b = title.lower(), mid.lower(), body.lower()
    s = 0
    if phrase in t:
        s += 100 + (50 if t.startswith(phrase) else 0)
    for term in terms:
        if term in t:
            s += 30
        if term in m:
            s += 12
        if term in b:
            s += 4
    return s


def search(
    db: Session,
    q: str,
    types: list[str] | None = None,
    category: str | None = None,
    skill_id: int | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    phrase = q.strip().lower()
    terms = [t for t in re.split(r"\s+", phrase) if t]
    if not terms:
        return []
    wanted = set(types or TYPES)
    results: list[dict[str, Any]] = []
    skill_names = dict(db.execute(select(Skill.id, Skill.name)).all())

    def like_all(*cols):
        # every term must appear in at least one of the columns
        return [or_(*[c.ilike(f"%{term}%") for c in cols]) for term in terms]

    def add(kind, id_, title, mid, body, url, meta):
        score = _score(title, mid, body, terms, phrase)
        if score:
            results.append({"type": kind, "id": id_, "title": title, "snippet": _snippet(mid or body, terms) if mid and any(
                t in mid.lower() for t in terms) else _snippet(body or mid, terms), "url": url, "score": score, **meta})

    if "skill" in wanted:
        stmt = select(Skill).where(*like_all(Skill.name, Skill.category, Skill.description, Skill.evidence_notes))
        if category:
            stmt = stmt.where(Skill.category == category)
        if skill_id:
            stmt = stmt.where(Skill.id == skill_id)
        for s in db.scalars(stmt.limit(limit)):
            add("skill", s.id, s.name, s.category + " " + s.description, s.evidence_notes, f"/skills/{s.id}",
                {"category": s.category, "skill": s.name})

    if "topic" in wanted:
        stmt = select(Topic).where(*like_all(
            Topic.title, Topic.summary, Topic.category, Topic.details_md, Topic.code_md, Topic.notes_md,
            Topic.mistakes_md, Topic.best_practices_md, Topic.use_cases_md, Topic.my_context_md))
        if category:
            stmt = stmt.where(Topic.category == category)
        if skill_id:
            stmt = stmt.where(Topic.skill_id == skill_id)
        for t in db.scalars(stmt.limit(limit)):
            body = " ".join([t.details_md, t.code_md, t.notes_md, t.mistakes_md, t.best_practices_md])
            add("topic", t.id, t.title, t.summary + " " + " ".join(t.tags or []), body, f"/topics/{t.id}",
                {"category": t.category, "skill": skill_names.get(t.skill_id), "status": t.status})

    if "exercise" in wanted:
        stmt = select(Exercise).where(*like_all(Exercise.title, Exercise.problem_md, Exercise.category))
        if category:
            stmt = stmt.where(Exercise.category == category)
        if skill_id:
            stmt = stmt.where(Exercise.skill_id == skill_id)
        for e in db.scalars(stmt.limit(limit)):
            add("exercise", e.id, e.title, " ".join(e.concepts or []) + " " + e.category, e.problem_md,
                f"/practice/{e.id}", {"category": e.category, "skill": skill_names.get(e.skill_id), "difficulty": e.difficulty})

    if "interview" in wanted:
        stmt = select(InterviewQuestion).where(*like_all(
            InterviewQuestion.question, InterviewQuestion.answer_md, InterviewQuestion.my_answer_md))
        if category:
            stmt = stmt.where(InterviewQuestion.category == category)
        if skill_id:
            stmt = stmt.where(InterviewQuestion.skill_id == skill_id)
        for iq in db.scalars(stmt.limit(limit)):
            add("interview", iq.id, iq.question, iq.category, iq.answer_md + " " + iq.my_answer_md,
                f"/interview?id={iq.id}", {"category": iq.category, "skill": skill_names.get(iq.skill_id),
                                          "difficulty": iq.difficulty})

    if "project" in wanted:
        cols = (Project.name, Project.summary, Project.architecture_md, Project.modules_md, Project.business_logic_md,
                Project.challenges_md, Project.solutions_md, Project.learnings_md, Project.database_md)
        stmt = select(Project).where(*like_all(*cols))
        for p in db.scalars(stmt.limit(limit)):
            body = " ".join([p.architecture_md, p.modules_md, p.business_logic_md, p.challenges_md, p.solutions_md,
                             p.learnings_md, p.database_md])
            add("project", p.id, p.name, p.summary + " " + " ".join(p.tech or []), body, f"/projects/{p.id}",
                {"category": p.kind})

    if "roadmap" in wanted:
        stmt = select(RoadmapItem).where(*like_all(RoadmapItem.title, RoadmapItem.why_md, RoadmapItem.mini_projects_md))
        if skill_id:
            stmt = stmt.where(RoadmapItem.skill_id == skill_id)
        for r in db.scalars(stmt.limit(limit)):
            add("roadmap", r.id, r.title, r.track, r.why_md + " " + r.mini_projects_md, (f"/learn/{r.track}#{r.slug}" if r.track in ("python", "backend", "ai") else "/targets"),
                {"category": r.track, "skill": skill_names.get(r.skill_id)})

    if "flashcard" in wanted:
        stmt = select(Flashcard).where(*like_all(Flashcard.front, Flashcard.back))
        if skill_id:
            stmt = stmt.where(Flashcard.skill_id == skill_id)
        for c in db.scalars(stmt.limit(limit)):
            add("flashcard", c.id, c.front, "", c.back, f"/topics/{c.topic_id}" if c.topic_id else "/revision",
                {"skill": skill_names.get(c.skill_id)})

    results.sort(key=lambda r: -r["score"])
    return results[:limit]
