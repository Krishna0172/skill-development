"""SQLAlchemy models — the single source of truth for all tracked data.

Design rules
- Every learning artefact links back to a Skill (and usually a Topic), so statistics can be
  sliced per technology.
- Progress is never overwritten silently: status/proficiency changes are appended to
  `history_events`, reviews to `review_logs`, attempts to `exercise_attempts`.
- Derived numbers (durations, completion %, streaks) are computed from these records at
  read time, never stored as counters that could drift.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, now

# --------------------------------------------------------------------------- associations

topic_extra_skills = Table(
    "topic_extra_skills",
    Base.metadata,
    Column("topic_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
)

topic_links = Table(
    "topic_links",
    Base.metadata,
    Column("topic_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
    Column("related_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
)

exercise_topics = Table(
    "exercise_topics",
    Base.metadata,
    Column("exercise_id", ForeignKey("exercises.id", ondelete="CASCADE"), primary_key=True),
    Column("topic_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
)

project_skills = Table(
    "project_skills",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
)

project_topics = Table(
    "project_topics",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("topic_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


# --------------------------------------------------------------------------- skills


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # demonstrated | resume | possibly_familiar | not_demonstrated | needs_revision | to_learn
    classification: Mapped[str] = mapped_column(String(20), default="to_learn", index=True)
    evidence_notes: Mapped[str] = mapped_column(Text, default="")
    # learning track: python | backend | ai | none
    track: Mapped[str] = mapped_column(String(20), default="none", index=True)
    # not_started | learning | revising | confident | paused
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    # Self-assessed 1-6 (Unfamiliar … Expert); None = not assessed yet (never guessed by the app)
    proficiency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_proficiency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    next_action: Mapped[str] = mapped_column(Text, default="")
    practical_experience: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="user")

    topics: Mapped[list[Topic]] = relationship(back_populates="skill", foreign_keys="Topic.skill_id")


# --------------------------------------------------------------------------- knowledge base


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True, default="General")
    difficulty: Mapped[str] = mapped_column(String(20), default="intermediate")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    details_md: Mapped[str] = mapped_column(Text, default="")
    code_md: Mapped[str] = mapped_column(Text, default="")
    use_cases_md: Mapped[str] = mapped_column(Text, default="")
    mistakes_md: Mapped[str] = mapped_column(Text, default="")
    best_practices_md: Mapped[str] = mapped_column(Text, default="")
    my_context_md: Mapped[str] = mapped_column(Text, default="")
    why_md: Mapped[str] = mapped_column(Text, default="")
    revision_notes_md: Mapped[str] = mapped_column(Text, default="")
    exercises_md: Mapped[str] = mapped_column(Text, default="")
    notes_md: Mapped[str] = mapped_column(Text, default="")  # personal notes
    track: Mapped[str] = mapped_column(String(20), default="none", index=True)  # python | backend | ai | none
    # not_started | learning | practised | learned | needs_revision | difficult | mastered
    # "learned" and "mastered" require demonstrated understanding (see services/topics.py)
    status: Mapped[str] = mapped_column(String(20), default="not_started", index=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="user")

    skill: Mapped[Skill | None] = relationship(back_populates="topics", foreign_keys=[skill_id])
    extra_skills: Mapped[list[Skill]] = relationship(secondary=topic_extra_skills)
    related: Mapped[list[Topic]] = relationship(
        secondary=topic_links,
        primaryjoin=lambda: Topic.id == topic_links.c.topic_id,
        secondaryjoin=lambda: Topic.id == topic_links.c.related_id,
    )
    flashcards: Mapped[list[Flashcard]] = relationship(back_populates="topic", cascade="all, delete-orphan")
    questions: Mapped[list[Question]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="Question.position"
    )


class Flashcard(TimestampMixin, Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="user")

    topic: Mapped[Topic | None] = relationship(back_populates="flashcards")


class Question(TimestampMixin, Base):
    """Knowledge-check question. Auto-graded: mcq, fill_blank, output. Self-graded against a
    rubric: debug, explain, qa."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    type: Mapped[str] = mapped_column(String(12), index=True)  # mcq | fill_blank | output | debug | explain | qa
    prompt: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answers: Mapped[list] = mapped_column(JSON, default=list)  # accepted answers (fill_blank / output)
    answer_md: Mapped[str] = mapped_column(Text, default="")  # model answer (self-graded types)
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(10), default="user")

    topic: Mapped[Topic | None] = relationship(back_populates="questions")


class AssessmentAttempt(Base):
    """One run of a knowledge check: a topic assessment, a mixed quiz, or a mock interview.
    `passed` on a topic assessment is the evidence required to mark a topic learned."""

    __tablename__ = "assessment_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_ref: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="assessment")  # assessment | quiz | mock_interview
    role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    total: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)  # points earned (self-graded items give partial credit)
    score_pct: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[list] = mapped_column(JSON, default=list)  # [{question_id, type, correct|points, response}]


class InterviewQuestion(TimestampMixin, Base):
    __tablename__ = "interview_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    difficulty: Mapped[str] = mapped_column(String(10), default="medium")
    question: Mapped[str] = mapped_column(Text)
    answer_md: Mapped[str] = mapped_column(Text, default="")
    my_answer_md: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    roles: Mapped[list] = mapped_column(JSON, default=list)  # python-dev | python-backend | backend-engineer | ai-engineer | genai-engineer
    project_name: Mapped[str] = mapped_column(String(120), default="")  # work service the question is about
    # new | practising | confident
    status: Mapped[str] = mapped_column(String(20), default="new")
    practice_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_practised_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="user")


# --------------------------------------------------------------------------- revision engine


class ReviewItem(Base):
    """Spaced-repetition state for one reviewable thing (topic, flashcard, interview question)."""

    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("item_type", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_type: Mapped[str] = mapped_column(String(20))  # topic | flashcard | interview
    item_id: Mapped[int] = mapped_column(Integer)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    success_streak: Mapped[int] = mapped_column(Integer, default=0)
    ease: Mapped[float] = mapped_column(Float, default=2.5)  # per-item difficulty multiplier (1.3–3.0)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ReviewLog(Base):
    """Append-only record of every revision answer. Survives deletion of the review item."""

    __tablename__ = "review_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_ref: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    review_item_id: Mapped[int | None] = mapped_column(ForeignKey("review_items.id", ondelete="SET NULL"))
    item_type: Mapped[str] = mapped_column(String(20))
    item_id: Mapped[int] = mapped_column(Integer)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    rating: Mapped[str] = mapped_column(String(20))  # easy | need_practice | difficult | forgot
    remembered: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_before: Mapped[int] = mapped_column(Integer)
    stage_after: Mapped[int] = mapped_column(Integer)
    interval_days: Mapped[int] = mapped_column(Integer)
    next_due: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------- coding practice


class Exercise(TimestampMixin, Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30), index=True)
    language: Mapped[str] = mapped_column(String(20))  # python | sql | javascript | text
    difficulty: Mapped[str] = mapped_column(String(10), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    concepts: Mapped[list] = mapped_column(JSON, default=list)
    problem_md: Mapped[str] = mapped_column(Text)
    examples: Mapped[list] = mapped_column(JSON, default=list)
    hints: Mapped[list] = mapped_column(JSON, default=list)
    starter_code: Mapped[str] = mapped_column(Text, default="")
    solution_code: Mapped[str] = mapped_column(Text, default="")
    solution_md: Mapped[str] = mapped_column(Text, default="")
    tests: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="user")

    topics: Mapped[list[Topic]] = relationship(secondary=exercise_topics)
    attempts: Mapped[list[ExerciseAttempt]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan", order_by="ExerciseAttempt.submitted_at"
    )


class ExerciseAttempt(Base):
    __tablename__ = "exercise_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_ref: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id", ondelete="CASCADE"), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    language: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(Text, default="")
    # passed | failed | error | self_solved | self_failed | gave_up
    outcome: Mapped[str] = mapped_column(String(20))
    tests_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    solution_viewed: Mapped[bool] = mapped_column(Boolean, default=False)
    # independent = solved without hints or viewing the solution (computed at submit time)
    solved_independently: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    learned_md: Mapped[str] = mapped_column(Text, default="")  # "what I learned"
    notes: Mapped[str] = mapped_column(Text, default="")

    exercise: Mapped[Exercise] = relationship(back_populates="attempts")


# --------------------------------------------------------------------------- projects


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="personal")  # professional | personal
    organization: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(120), default="")
    # planned | active | paused | completed | archived | unknown
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    tech: Mapped[list] = mapped_column(JSON, default=list)
    purpose_md: Mapped[str] = mapped_column(Text, default="")
    architecture_md: Mapped[str] = mapped_column(Text, default="")
    folder_structure_md: Mapped[str] = mapped_column(Text, default="")
    modules_md: Mapped[str] = mapped_column(Text, default="")
    database_md: Mapped[str] = mapped_column(Text, default="")
    apis_md: Mapped[str] = mapped_column(Text, default="")
    business_logic_md: Mapped[str] = mapped_column(Text, default="")
    challenges_md: Mapped[str] = mapped_column(Text, default="")
    solutions_md: Mapped[str] = mapped_column(Text, default="")
    contributions_md: Mapped[str] = mapped_column(Text, default="")
    learnings_md: Mapped[str] = mapped_column(Text, default="")
    pending_improvements_md: Mapped[str] = mapped_column(Text, default="")
    important_files_md: Mapped[str] = mapped_column(Text, default="")
    important_functions_md: Mapped[str] = mapped_column(Text, default="")
    revise_md: Mapped[str] = mapped_column(Text, default="")  # "what I need to revise"
    source_path: Mapped[str] = mapped_column(String(400), default="")
    evidence_note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="user")

    skills: Mapped[list[Skill]] = relationship(secondary=project_skills)
    topics: Mapped[list[Topic]] = relationship(secondary=project_topics)
    items: Mapped[list[ProjectItem]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectItem.id"
    )


class ProjectItem(TimestampMixin, Base):
    __tablename__ = "project_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # module | task | feature | bug | improvement
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo | in_progress | done
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="items")


# --------------------------------------------------------------------------- roadmap & goals


class RoadmapItem(TimestampMixin, Base):
    __tablename__ = "roadmap_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    # strengthen | rarely_used | to_learn | advanced | industry | projects
    track: Mapped[str] = mapped_column(String(20), index=True)  # python | backend | ai | projects
    area: Mapped[str | None] = mapped_column(String(20), nullable=True)  # python | postgresql | backend | aws | ai
    module_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(String(20), default="new")  # demonstrated | revise | deepen | new
    level: Mapped[str] = mapped_column(String(20), default="intermediate")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    why_md: Mapped[str] = mapped_column(Text, default="")
    prerequisites_md: Mapped[str] = mapped_column(Text, default="")
    exercises_md: Mapped[str] = mapped_column(Text, default="")
    mini_projects_md: Mapped[str] = mapped_column(Text, default="")
    outcome_md: Mapped[str] = mapped_column(Text, default="")
    # not_started | in_progress | completed | skipped
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="user")

    steps: Mapped[list[RoadmapStep]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="RoadmapStep.sort_order"
    )


class RoadmapStep(Base):
    __tablename__ = "roadmap_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("roadmap_items.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    item: Mapped[RoadmapItem] = relationship(back_populates="steps")


class Goal(TimestampMixin, Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")  # low | medium | high
    # not_started | in_progress | completed | abandoned
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    # manual | exercises_solved | topics_learned | hours | reviews_done | roadmap_item
    metric: Mapped[str] = mapped_column(String(30), default="manual")
    metric_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    # optional filters: {"category": "sql"} / {"roadmap_item_id": 3}; skill_id above also filters
    metric_filter: Mapped[dict] = mapped_column(JSON, default=dict)
    count_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    manual_progress: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- tracking


class LearningSession(TimestampMixin, Base):
    """A block of time. Timer sessions have ended_at=None while running."""

    __tablename__ = "learning_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_ref: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # learning | practice | revision | project | reading | other
    kind: Mapped[str] = mapped_column(String(20), default="learning", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("exercises.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(10), default="manual")  # timer | manual
    notes: Mapped[str] = mapped_column(Text, default="")  # concepts learned / what I did
    problems: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    next_action: Mapped[str] = mapped_column(Text, default="")


class Target(TimestampMixin, Base):
    """Daily / weekly / monthly learning targets. Progress is computed from records."""

    __tablename__ = "targets"
    __table_args__ = (UniqueConstraint("period", "metric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(10))  # daily | weekly | monthly
    # minutes | reviews | exercises_solved | topics_passed | active_days
    metric: Mapped[str] = mapped_column(String(30))
    value: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ResumeClaim(TimestampMixin, Base):
    """A resume claim with the evidence behind it and your STAR story for interviews."""

    __tablename__ = "resume_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    claim: Mapped[str] = mapped_column(Text)
    evidence_md: Mapped[str] = mapped_column(Text, default="")
    # supported | partially-supported | needs-preparation | ready (you've written & rehearsed it)
    status: Mapped[str] = mapped_column(String(30), default="needs-preparation")
    likely_questions: Mapped[list] = mapped_column(JSON, default=list)
    star_prompts: Mapped[dict] = mapped_column(JSON, default=dict)
    star_situation: Mapped[str] = mapped_column(Text, default="")
    star_task: Mapped[str] = mapped_column(Text, default="")
    star_action: Mapped[str] = mapped_column(Text, default="")
    star_result: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="user")


class DailyLog(TimestampMixin, Base):
    """End-of-day journal (one per date)."""

    __tablename__ = "daily_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    log_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    studied_md: Mapped[str] = mapped_column(Text, default="")
    accomplished_md: Mapped[str] = mapped_column(Text, default="")
    problems_md: Mapped[str] = mapped_column(Text, default="")
    tomorrow_md: Mapped[str] = mapped_column(Text, default="")
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5


class HistoryEvent(Base):
    """Append-only journal of everything that happened: status changes, reviews, attempts…"""

    __tablename__ = "history_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    skill_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    field: Mapped[str | None] = mapped_column(String(40), nullable=True)
    old_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str] = mapped_column(String(400), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- app meta


class AppMeta(Base):
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class SeedRegistry(Base):
    """Remembers which seed items were ever inserted so deleted ones are not re-added."""

    __tablename__ = "seed_registry"

    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), primary_key=True)
    seeded_at: Mapped[datetime] = mapped_column(DateTime, default=now)
