"""Knowledge checks: grading questions and recording assessment attempts.

Grading
- mcq          correct option index                                    (auto)
- fill_blank   normalised text equals any accepted answer              (auto)
- output       stdout prediction equals the expected output            (auto)
- debug/explain/qa  self-graded: the learner ticks which rubric points they covered;
                    points = ticked / rubric size (honest self-marking, shown next to the model answer)

A topic assessment is PASSED at >= PASS_PCT. Passing is the evidence that lets a topic become
"learned"; missed auto-graded questions are put into the spaced-repetition queue.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import history
from ..enums import AUTO_GRADED
from ..models import AssessmentAttempt, Question, ReviewItem, Topic
from . import revision

PASS_PCT = 70.0
MASTERY_PCT = 85.0


def normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def normalise_output(value: str) -> str:
    lines = [ln.rstrip() for ln in (value or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def grade(question: Question, response: str = "", rubric_ticks: list[int] | None = None) -> dict[str, Any]:
    """Pure grading of one answer. Returns {points (0..1), correct (bool|None), auto (bool)}."""
    qtype = question.type
    if qtype == "mcq":
        try:
            correct = int(response) == question.answer_index
        except (TypeError, ValueError):
            correct = False
        return {"points": 1.0 if correct else 0.0, "correct": correct, "auto": True}
    if qtype == "fill_blank":
        given = normalise_text(response)
        correct = bool(given) and any(given == normalise_text(a) for a in question.answers)
        return {"points": 1.0 if correct else 0.0, "correct": correct, "auto": True}
    if qtype == "output":
        given = normalise_output(response)
        correct = any(given == normalise_output(a) for a in question.answers)
        return {"points": 1.0 if correct else 0.0, "correct": correct, "auto": True}
    # self-graded
    rubric = question.rubric or ["Covered the model answer's main point"]
    ticks = {i for i in (rubric_ticks or []) if 0 <= i < len(rubric)}
    points = round(len(ticks) / len(rubric), 3)
    return {"points": points, "correct": None if points < 1 else True, "auto": False}


def record_attempt(
    db: Session,
    responses: list[dict[str, Any]],
    *,
    topic_id: int | None = None,
    mode: str = "assessment",
    role: str | None = None,
    duration_seconds: int | None = None,
    client_ref: str | None = None,
) -> AssessmentAttempt:
    """Grade all responses, store the attempt, and apply its consequences."""
    ids = [r["question_id"] for r in responses]
    questions = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(ids)))}
    missing = set(ids) - set(questions)
    if missing:
        raise LookupError(f"Unknown question ids: {sorted(missing)}")
    if topic_id is not None and any(questions[i].topic_id != topic_id for i in ids):
        raise ValueError("All questions in a topic assessment must belong to that topic")

    details, score = [], 0.0
    for r in responses:
        q = questions[r["question_id"]]
        g = grade(q, r.get("response", ""), r.get("rubric_ticks"))
        score += g["points"]
        details.append({"question_id": q.id, "type": q.type, "points": g["points"], "correct": g["correct"],
                        "response": (r.get("response") or "")[:2000], "rubric_ticks": r.get("rubric_ticks") or []})
    total = len(responses)
    pct = round(100 * score / total, 1) if total else 0.0
    topic = db.get(Topic, topic_id) if topic_id else None
    attempt = AssessmentAttempt(
        client_ref=client_ref, mode=mode, role=role, topic_id=topic_id,
        skill_id=topic.skill_id if topic else None, total=total, score=round(score, 2),
        score_pct=pct, passed=pct >= PASS_PCT, duration_seconds=duration_seconds, details=details,
    )
    db.add(attempt)
    db.flush()

    label = topic.title if topic else (f"mock interview ({role})" if role else "mixed quiz")
    history.record(db, "assessment", "assessment", attempt.id,
                   f"{'Assessment' if topic else mode.replace('_', ' ').title()}: {label} — {pct}%"
                   + (" (passed)" if attempt.passed and topic else ""),
                   skill_id=attempt.skill_id, topic_id=topic_id,
                   details={"score_pct": pct, "passed": attempt.passed, "mode": mode})

    # Missed auto-graded questions come back through spaced repetition.
    for d in details:
        if d["type"] in AUTO_GRADED and not d["correct"]:
            revision.schedule(db, "question", d["question_id"])

    if topic is not None and mode == "assessment":
        _apply_to_topic(db, topic, attempt)
    return attempt


def _apply_to_topic(db: Session, topic: Topic, attempt: AssessmentAttempt) -> None:
    from . import topics as topic_service

    if attempt.passed:
        if topic.status not in {"learned", "mastered"}:
            topic_service.set_status(db, topic, "learned", f"passed assessment with {attempt.score_pct}%")
    elif topic.status in {"learned", "mastered"}:
        topic_service.set_status(db, topic, "needs_revision", f"assessment score {attempt.score_pct}%")
    elif topic.status == "not_started":
        topic_service.set_status(db, topic, "learning", "first assessment attempt")


def best_score(db: Session, topic_id: int) -> float | None:
    rows = db.scalars(select(AssessmentAttempt.score_pct).where(
        AssessmentAttempt.topic_id == topic_id, AssessmentAttempt.mode == "assessment"))
    scores = list(rows)
    return max(scores) if scores else None


def has_passed(db: Session, topic_id: int, threshold: float = PASS_PCT) -> bool:
    best = best_score(db, topic_id)
    return best is not None and best >= threshold


def mastery_evidence(db: Session, topic: Topic) -> tuple[bool, str]:
    """Mastery = strong assessment score AND proven recall over time."""
    best = best_score(db, topic.id)
    if best is None or best < MASTERY_PCT:
        return False, f"needs an assessment score of at least {MASTERY_PCT:.0f}% (best so far: {best if best is not None else 'none'})"
    item = db.scalar(select(ReviewItem).where(ReviewItem.item_type == "topic", ReviewItem.item_id == topic.id))
    if not item or item.stage < 3:
        return False, "needs successful spaced revisions up to the 14-day interval"
    return True, ""


def weak_question_ids(db: Session, limit: int = 10, *, auto_only: bool = True) -> list[int]:
    """Question ids the learner misses most, from past attempt details (most-missed first).

    A question counts as missed when it was auto-graded wrong or self-graded below PASS_PCT."""
    from collections import Counter

    seen: Counter[int] = Counter()
    missed: Counter[int] = Counter()
    for (details,) in db.execute(select(AssessmentAttempt.details)):
        for d in details or []:
            qid = d.get("question_id")
            if not qid:
                continue
            seen[qid] += 1
            pts = d.get("points")
            wrong = (d.get("correct") is False) or (pts is not None and pts < PASS_PCT / 100)
            if wrong:
                missed[qid] += 1
    ranked = [q for q, _ in sorted(missed.items(), key=lambda kv: (-kv[1], -(kv[1] / seen[kv[0]])))]
    if not ranked:
        return []
    stmt = select(Question.id, Question.type).where(Question.id.in_(ranked))
    ok = {qid for qid, qtype in db.execute(stmt) if not auto_only or qtype in AUTO_GRADED}
    return [q for q in ranked if q in ok][:limit]
