"""Allowed values for enum-like string columns, shared by schemas and services."""

from typing import Literal

SkillCategory = str  # free text; seeded: Programming Languages, Backend, Frontend, Database, ...
Classification = Literal["demonstrated", "resume", "possibly_familiar", "not_demonstrated", "needs_revision", "to_learn"]
SkillTrack = Literal["python", "backend", "ai", "none"]
SkillStatus = Literal["not_started", "learning", "revising", "confident", "paused"]
PROFICIENCY_LABELS = {1: "Unfamiliar", 2: "Beginner", 3: "Basic working knowledge", 4: "Intermediate", 5: "Advanced", 6: "Expert"}
QuestionType = Literal["mcq", "fill_blank", "output", "debug", "explain", "qa"]
AUTO_GRADED = {"mcq", "fill_blank", "output"}
ROLES = {
    "python-dev": "Python Developer",
    "python-backend": "Python Backend Developer",
    "backend-engineer": "Backend Engineer",
    "ai-engineer": "AI Engineer",
    "genai-engineer": "Generative AI Engineer",
}
TargetPeriod = Literal["daily", "weekly", "monthly"]
TargetMetric = Literal["minutes", "reviews", "exercises_solved", "topics_passed", "active_days"]

TopicStatus = Literal["not_started", "learning", "practised", "learned", "needs_revision", "difficult", "mastered"]
TOPIC_STATUSES: tuple[str, ...] = TopicStatus.__args__  # type: ignore[attr-defined]
TOPIC_DONE_STATUSES = {"learned", "mastered"}
Difficulty3 = Literal["beginner", "intermediate", "advanced"]
Difficulty = Literal["easy", "medium", "hard"]

ReviewItemType = Literal["topic", "flashcard", "interview", "question"]
Rating = Literal["easy", "need_practice", "difficult", "forgot"]

ExerciseCategory = Literal["python-fundamentals", "advanced-python", "dsa", "sql", "postgresql", "backend", "api",
                           "ai-engineering", "refactoring"]
EXERCISE_CATEGORY_LABELS = {
    "python-fundamentals": "Python fundamentals", "advanced-python": "Advanced Python", "dsa": "Data structures & algorithms",
    "sql": "SQL", "postgresql": "PostgreSQL", "backend": "Backend development", "api": "API development",
    "ai-engineering": "AI Engineering", "refactoring": "Refactor your own habits",
}
ExerciseLanguage = Literal["python", "sql", "javascript", "text"]
AttemptOutcome = Literal["passed", "failed", "error", "self_solved", "self_failed", "gave_up"]
SOLVED_OUTCOMES = {"passed", "self_solved"}

InterviewStatus = Literal["new", "practising", "confident"]
ProjectKind = Literal["professional", "personal"]
ProjectStatus = Literal["planned", "active", "paused", "completed", "archived", "unknown"]
ProjectItemKind = Literal["module", "task", "feature", "bug", "improvement"]
ProjectItemStatus = Literal["todo", "in_progress", "done"]

RoadmapTrack = Literal["strengthen", "rarely_used", "to_learn", "advanced", "industry", "projects"]
RoadmapLevel = Literal["beginner", "intermediate", "advanced"]
RoadmapStatus = Literal["not_started", "in_progress", "completed", "skipped"]

GoalPriority = Literal["low", "medium", "high"]
GoalStatus = Literal["not_started", "in_progress", "completed", "abandoned"]
GoalMetric = Literal["manual", "exercises_solved", "topics_learned", "hours", "reviews_done", "roadmap_item"]

SessionKind = Literal["learning", "practice", "revision", "project", "reading", "other"]
