"""Pydantic request models (validation at the API boundary).

Responses are built explicitly in the routers as plain dicts so computed fields (status,
progress, durations) sit next to stored ones.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AttemptOutcome,
    Difficulty,
    Difficulty3,
    Classification,
    QuestionType,
    TargetMetric,
    TargetPeriod,
    ExerciseCategory,
    ExerciseLanguage,
    GoalMetric,
    GoalPriority,
    GoalStatus,
    InterviewStatus,
    ProjectItemKind,
    ProjectItemStatus,
    ProjectKind,
    ProjectStatus,
    Rating,
    ReviewItemType,
    RoadmapLevel,
    RoadmapStatus,
    RoadmapTrack,
    SessionKind,
    SkillStatus,
    SkillTrack,
    TopicStatus,
)

Name = Annotated[str, Field(min_length=1, max_length=200)]
Level16 = Annotated[int, Field(ge=1, le=6)]
Level15 = Annotated[int, Field(ge=1, le=5)]
LongText = Annotated[str, Field(max_length=200_000)]
ClientRef = Annotated[str, Field(min_length=8, max_length=64)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- skills


class SkillCreate(_In):
    name: Name
    category: Annotated[str, Field(min_length=1, max_length=40)]
    description: LongText = ""
    track: SkillTrack = "none"
    status: SkillStatus = "not_started"
    classification: Classification = "to_learn"
    proficiency: Level16 | None = None
    target_proficiency: Level16 | None = None
    confidence: Level15 | None = None
    next_action: LongText = ""
    practical_experience: LongText = ""
    notes: LongText = ""


class SkillUpdate(_In):
    name: Name | None = None
    category: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    description: LongText | None = None
    classification: Classification | None = None
    evidence_notes: LongText | None = None
    track: SkillTrack | None = None
    status: SkillStatus | None = None
    proficiency: Level16 | None = None
    target_proficiency: Level16 | None = None
    confidence: Level15 | None = None
    next_action: LongText | None = None
    practical_experience: LongText | None = None
    notes: LongText | None = None
    is_archived: bool | None = None
    change_note: Annotated[str, Field(max_length=300)] = ""


# --------------------------------------------------------------------------- knowledge


class TopicBase(_In):
    title: Name | None = None
    skill_id: int | None = None
    category: Annotated[str, Field(max_length=40)] | None = None
    difficulty: Difficulty3 | None = None
    tags: list[Annotated[str, Field(max_length=40)]] | None = None
    summary: LongText | None = None
    details_md: LongText | None = None
    code_md: LongText | None = None
    use_cases_md: LongText | None = None
    mistakes_md: LongText | None = None
    best_practices_md: LongText | None = None
    my_context_md: LongText | None = None
    notes_md: LongText | None = None
    status: TopicStatus | None = None
    confidence: Level15 | None = None
    extra_skill_ids: list[int] | None = None
    related_ids: list[int] | None = None


class TopicCreate(TopicBase):
    title: Name
    category: Annotated[str, Field(min_length=1, max_length=40)] = "General"
    add_to_revision: bool = False


class TopicUpdate(TopicBase):
    change_note: Annotated[str, Field(max_length=300)] = ""


class FlashcardIn(_In):
    topic_id: int | None = None
    skill_id: int | None = None
    front: Annotated[str, Field(min_length=1, max_length=5000)]
    back: Annotated[str, Field(min_length=1, max_length=20000)]
    add_to_revision: bool = True


class FlashcardUpdate(_In):
    front: Annotated[str, Field(min_length=1, max_length=5000)] | None = None
    back: Annotated[str, Field(min_length=1, max_length=20000)] | None = None
    topic_id: int | None = None


class QuestionIn(_In):
    topic_id: int
    type: QuestionType
    prompt: Annotated[str, Field(min_length=1, max_length=10000)]
    code: LongText = ""
    options: list[Annotated[str, Field(min_length=1, max_length=1000)]] = []
    answer_index: int | None = None
    answers: list[Annotated[str, Field(max_length=5000)]] = []
    answer_md: LongText = ""
    rubric: list[Annotated[str, Field(max_length=500)]] = []
    explanation: LongText = ""

    @model_validator(mode="after")
    def _check(self):
        if self.type == "mcq":
            if len(self.options) < 2 or self.answer_index is None or not 0 <= self.answer_index < len(self.options):
                raise ValueError("mcq needs 2+ options and a valid answer_index")
        elif self.type in ("fill_blank", "output"):
            if not self.answers:
                raise ValueError(f"{self.type} needs at least one accepted answer")
        elif not self.answer_md:
            raise ValueError(f"{self.type} needs a model answer (answer_md)")
        return self


class ResponseIn(_In):
    question_id: int
    # mcq: option index as string; fill_blank/output: text; self-graded: ignored (use ticks)
    response: Annotated[str, Field(max_length=20000)] = ""
    rubric_ticks: list[int] = []  # indices of rubric points the learner says they covered


class AssessmentIn(_In):
    client_ref: ClientRef | None = None
    topic_id: int | None = None
    mode: Annotated[str, Field(pattern="^(assessment|quiz|mock_interview)$")] = "assessment"
    role: Annotated[str, Field(max_length=30)] | None = None
    duration_seconds: Annotated[int, Field(ge=0, le=86_400)] | None = None
    responses: Annotated[list[ResponseIn], Field(min_length=1, max_length=200)]


class TargetIn(_In):
    period: TargetPeriod
    metric: TargetMetric
    value: Annotated[float, Field(gt=0, le=100_000)]
    is_active: bool = True


class ClaimUpdate(_In):
    status: Annotated[str, Field(pattern="^(supported|partially-supported|needs-preparation|ready)$")] | None = None
    star_situation: LongText | None = None
    star_task: LongText | None = None
    star_action: LongText | None = None
    star_result: LongText | None = None
    evidence_md: LongText | None = None


# --------------------------------------------------------------------------- revision


class ScheduleIn(_In):
    item_type: ReviewItemType
    item_id: int


class RateIn(_In):
    rating: Rating
    confidence: Level15 | None = None
    duration_seconds: Annotated[int, Field(ge=0, le=86_400)] | None = None
    note: Annotated[str, Field(max_length=2000)] = ""
    client_ref: ClientRef | None = None


# --------------------------------------------------------------------------- practice


class ExerciseIn(_In):
    title: Name
    category: ExerciseCategory
    language: ExerciseLanguage
    difficulty: Difficulty = "medium"
    skill_id: int | None = None
    concepts: list[Annotated[str, Field(max_length=60)]] = []
    topic_ids: list[int] = []
    problem_md: LongText
    examples: list[dict] = []
    hints: list[Annotated[str, Field(max_length=2000)]] = []
    starter_code: LongText = ""
    solution_code: LongText = ""
    solution_md: LongText = ""
    tests: dict | None = None


class ExerciseUpdate(_In):
    title: Name | None = None
    category: ExerciseCategory | None = None
    difficulty: Difficulty | None = None
    skill_id: int | None = None
    concepts: list[Annotated[str, Field(max_length=60)]] | None = None
    topic_ids: list[int] | None = None
    problem_md: LongText | None = None
    examples: list[dict] | None = None
    hints: list[Annotated[str, Field(max_length=2000)]] | None = None
    starter_code: LongText | None = None
    solution_code: LongText | None = None
    solution_md: LongText | None = None
    tests: dict | None = None


class RunIn(_In):
    code: Annotated[str, Field(max_length=100_000)]


class AttemptIn(_In):
    client_ref: ClientRef | None = None
    code: Annotated[str, Field(max_length=100_000)] = ""
    # Only for self-assessed attempts (text exercises, giving up, or no runner available)
    self_outcome: AttemptOutcome | None = None
    duration_seconds: Annotated[int, Field(ge=0, le=86_400)] | None = None
    hints_used: Annotated[int, Field(ge=0, le=20)] = 0
    notes: Annotated[str, Field(max_length=5000)] = ""
    learned_md: Annotated[str, Field(max_length=5000)] = ""
    retry_needed: bool = False

    @field_validator("self_outcome")
    @classmethod
    def _self_only(cls, v):
        if v in {"passed", "failed", "error"}:
            raise ValueError("passed/failed/error are decided by the test runner")
        return v


# --------------------------------------------------------------------------- interview


class InterviewIn(_In):
    category: Annotated[str, Field(min_length=1, max_length=30)]
    difficulty: Difficulty = "medium"
    question: Annotated[str, Field(min_length=1, max_length=5000)]
    answer_md: LongText = ""
    my_answer_md: LongText = ""
    topic_id: int | None = None
    skill_id: int | None = None
    project_id: int | None = None
    tags: list[Annotated[str, Field(max_length=40)]] = []
    roles: list[Annotated[str, Field(max_length=30)]] = []


class InterviewUpdate(_In):
    category: Annotated[str, Field(min_length=1, max_length=30)] | None = None
    difficulty: Difficulty | None = None
    question: Annotated[str, Field(min_length=1, max_length=5000)] | None = None
    answer_md: LongText | None = None
    my_answer_md: LongText | None = None
    topic_id: int | None = None
    skill_id: int | None = None
    project_id: int | None = None
    status: InterviewStatus | None = None


class PracticeIn(_In):
    confidence: Level15
    my_answer_md: LongText | None = None
    client_ref: ClientRef | None = None


# --------------------------------------------------------------------------- projects


class ProjectBase(_In):
    name: Name | None = None
    kind: ProjectKind | None = None
    organization: Annotated[str, Field(max_length=120)] | None = None
    role: Annotated[str, Field(max_length=120)] | None = None
    status: ProjectStatus | None = None
    start_date: date | None = None
    end_date: date | None = None
    summary: LongText | None = None
    tech: list[Annotated[str, Field(max_length=60)]] | None = None
    purpose_md: LongText | None = None
    architecture_md: LongText | None = None
    folder_structure_md: LongText | None = None
    modules_md: LongText | None = None
    database_md: LongText | None = None
    apis_md: LongText | None = None
    business_logic_md: LongText | None = None
    challenges_md: LongText | None = None
    solutions_md: LongText | None = None
    contributions_md: LongText | None = None
    learnings_md: LongText | None = None
    important_files_md: LongText | None = None
    important_functions_md: LongText | None = None
    revise_md: LongText | None = None
    pending_improvements_md: LongText | None = None
    source_path: Annotated[str, Field(max_length=400)] | None = None
    skill_ids: list[int] | None = None
    topic_ids: list[int] | None = None

    @model_validator(mode="after")
    def _dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ProjectCreate(ProjectBase):
    name: Name
    kind: ProjectKind = "personal"
    status: ProjectStatus = "active"


class ProjectUpdate(ProjectBase):
    pass


class ProjectItemIn(_In):
    kind: ProjectItemKind
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: LongText = ""
    status: ProjectItemStatus = "todo"


class ProjectItemUpdate(_In):
    kind: ProjectItemKind | None = None
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: LongText | None = None
    status: ProjectItemStatus | None = None


# --------------------------------------------------------------------------- roadmap & goals


class RoadmapItemIn(_In):
    title: Name
    skill_id: int | None = None
    track: RoadmapTrack
    level: RoadmapLevel = "intermediate"
    why_md: LongText = ""
    prerequisites_md: LongText = ""
    exercises_md: LongText = ""
    mini_projects_md: LongText = ""
    outcome_md: LongText = ""
    steps: list[Annotated[str, Field(min_length=1, max_length=300)]] = []


class RoadmapItemUpdate(_In):
    title: Name | None = None
    skill_id: int | None = None
    track: RoadmapTrack | None = None
    level: RoadmapLevel | None = None
    status: RoadmapStatus | None = None
    why_md: LongText | None = None
    prerequisites_md: LongText | None = None
    exercises_md: LongText | None = None
    mini_projects_md: LongText | None = None
    outcome_md: LongText | None = None
    sort_order: int | None = None


class RoadmapStepIn(_In):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    topic_id: int | None = None


class RoadmapStepUpdate(_In):
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    topic_id: int | None = None
    done: bool | None = None


class GoalBase(_In):
    title: Name | None = None
    description: LongText | None = None
    skill_id: int | None = None
    target_date: date | None = None
    priority: GoalPriority | None = None
    status: GoalStatus | None = None
    metric: GoalMetric | None = None
    metric_target: Annotated[float, Field(gt=0, le=100_000)] | None = None
    metric_filter: dict | None = None
    count_from: date | None = None
    manual_progress: Annotated[int, Field(ge=0, le=100)] | None = None


class GoalCreate(GoalBase):
    title: Name
    priority: GoalPriority = "medium"
    metric: GoalMetric = "manual"

    @model_validator(mode="after")
    def _target(self):
        if self.metric not in {"manual", "roadmap_item"} and not self.metric_target:
            raise ValueError("metric_target is required for this metric")
        if self.metric == "roadmap_item" and not (self.metric_filter or {}).get("roadmap_item_id"):
            raise ValueError("metric_filter.roadmap_item_id is required for a roadmap goal")
        return self


class GoalUpdate(GoalBase):
    pass


# --------------------------------------------------------------------------- tracking


class SessionBase(_In):
    kind: SessionKind | None = None
    title: Annotated[str, Field(max_length=200)] | None = None
    skill_id: int | None = None
    topic_id: int | None = None
    project_id: int | None = None
    exercise_id: int | None = None
    notes: LongText | None = None
    problems: LongText | None = None
    difficulty: Level15 | None = None
    confidence: Level15 | None = None
    next_action: LongText | None = None


class SessionCreate(SessionBase):
    client_ref: ClientRef | None = None
    kind: SessionKind = "learning"
    started_at: datetime
    ended_at: datetime
    allow_overlap: bool = False

    @model_validator(mode="after")
    def _times(self):
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        if (self.ended_at - self.started_at).total_seconds() > 16 * 3600:
            raise ValueError("A single session can't be longer than 16 hours")
        if self.started_at.tzinfo or self.ended_at.tzinfo:
            raise ValueError("Send local times without a timezone offset")
        return self


class SessionUpdate(SessionBase):
    started_at: datetime | None = None
    ended_at: datetime | None = None
    allow_overlap: bool = False


class TimerStartIn(SessionBase):
    kind: SessionKind = "learning"


class TimerStopIn(_In):
    notes: LongText | None = None
    problems: LongText | None = None


class DailyLogIn(_In):
    studied_md: LongText = ""
    accomplished_md: LongText = ""
    problems_md: LongText = ""
    tomorrow_md: LongText = ""
    energy: Level15 | None = None
