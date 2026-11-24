"""Statistics computed from raw records (never from stored counters).

Data volumes for one person stay small (thousands of rows over years), so aggregation is done
in Python for clarity and database portability.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import now
from ..enums import SOLVED_OUTCOMES, TOPIC_DONE_STATUSES
from ..models import (
    AppMeta,
    DailyLog,
    Exercise,
    ExerciseAttempt,
    Goal,
    HistoryEvent,
    LearningSession,
    Project,
    AssessmentAttempt,
    ReviewItem,
    ReviewLog,
    RoadmapItem,
    RoadmapStep,
    Skill,
    Topic,
)
from . import timecalc

RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}


# --------------------------------------------------------------------------- ranges


def tracking_start(db: Session) -> date:
    row = db.get(AppMeta, "tracking_started_on")
    return date.fromisoformat(row.value) if row else date.today()


def resolve_range(db: Session, range_key: str = "30d", start: date | None = None, end: date | None = None) -> tuple[date, date]:
    today = date.today()
    if range_key == "custom":
        if not start or not end or start > end:
            raise ValueError("Custom range needs start <= end")
        return start, end
    if range_key == "all":
        first_session = db.scalar(select(func.min(LearningSession.started_at)))
        candidates = [tracking_start(db)] + ([first_session.date()] if first_session else [])
        return min(candidates), today
    days = RANGE_DAYS.get(range_key, 30)
    return today - timedelta(days=days - 1), today


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _month_key(d: date | datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_ago(d: date, n: int) -> date:
    """First day of the month `n` months before d's month."""
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


# --------------------------------------------------------------------------- loaders


def _sessions(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[LearningSession]:
    q = select(LearningSession)
    if start is not None:
        q = q.where((LearningSession.ended_at.is_(None)) | (LearningSession.ended_at > start))
    if end is not None:
        q = q.where(LearningSession.started_at < end)
    return list(db.scalars(q))


def _in(dt: datetime, w: tuple[datetime, datetime]) -> bool:
    return w[0] <= dt < w[1]


# --------------------------------------------------------------------------- activity & streaks


def active_days(db: Session, start: date | None = None) -> set[date]:
    """Days with real learning activity: a session, a revision, an exercise attempt or a quiz."""
    since = datetime.combine(start, time.min) if start else datetime.min
    days: set[date] = set()
    n = now()
    for s in _sessions(db, since if start else None):
        for d, (a, b) in timecalc.split_by_day(*timecalc.session_interval(s, n)):
            if (b - a).total_seconds() >= 60:
                days.add(d)
    for model, col in ((ReviewLog, ReviewLog.reviewed_at), (ExerciseAttempt, ExerciseAttempt.submitted_at),
                       (AssessmentAttempt, AssessmentAttempt.taken_at)):
        for (ts,) in db.execute(select(col).where(col >= since)):
            days.add(ts.date())
    return days


def streaks(db: Session) -> dict[str, Any]:
    days = active_days(db)
    today = date.today()
    current = 0
    cursor = today if today in days else today - timedelta(days=1)  # today not over yet
    while cursor in days:
        current += 1
        cursor -= timedelta(days=1)
    longest = run = 0
    prev: date | None = None
    for d in sorted(days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    return {"current": current, "longest": longest, "active_today": today in days, "total_active_days": len(days)}


# --------------------------------------------------------------------------- per-skill


def _exercise_status(db: Session) -> dict[int, str]:
    """exercise_id -> solved | attempted (pending exercises are absent)."""
    out: dict[int, str] = {}
    for ex_id, outcome in db.execute(select(ExerciseAttempt.exercise_id, ExerciseAttempt.outcome)):
        if outcome in SOLVED_OUTCOMES:
            out[ex_id] = "solved"
        else:
            out.setdefault(ex_id, "attempted")
    return out


def skill_overview(db: Session) -> dict[int, dict[str, Any]]:
    """Per-skill aggregates used by the skills list and dashboard."""
    n = now()
    agg: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "topics_total": 0, "topics_done": 0, "topics_attention": 0, "topics_revised": 0,
        "exercises_total": 0, "exercises_solved": 0, "seconds": 0,
        "last_studied_at": None, "last_revised_at": None, "due_reviews": 0,
    })
    topic_skill: dict[int, set[int]] = {}
    for t in db.scalars(select(Topic)):
        skills = {t.skill_id} if t.skill_id else set()
        skills |= {s.id for s in t.extra_skills}
        topic_skill[t.id] = skills
        for sid in skills:
            a = agg[sid]
            a["topics_total"] += 1
            if t.status in TOPIC_DONE_STATUSES:
                a["topics_done"] += 1
            if t.status in {"needs_revision", "difficult"}:
                a["topics_attention"] += 1
    revised_topics: dict[int, set[int]] = defaultdict(set)
    for skill_id, topic_id, ts in db.execute(select(ReviewLog.skill_id, ReviewLog.topic_id, ReviewLog.reviewed_at)):
        if skill_id is None:
            continue
        a = agg[skill_id]
        if topic_id:
            revised_topics[skill_id].add(topic_id)
        if a["last_revised_at"] is None or ts > a["last_revised_at"]:
            a["last_revised_at"] = ts
    for sid, ts in revised_topics.items():
        agg[sid]["topics_revised"] = len(ts)
    today = date.today()
    for (skill_id,) in db.execute(
        select(ReviewItem.skill_id).where(ReviewItem.is_active.is_(True), ReviewItem.due_date <= today)
    ):
        if skill_id:
            agg[skill_id]["due_reviews"] += 1
    status = _exercise_status(db)
    for ex_id, skill_id in db.execute(select(Exercise.id, Exercise.skill_id)):
        if skill_id is None:
            continue
        agg[skill_id]["exercises_total"] += 1
        if status.get(ex_id) == "solved":
            agg[skill_id]["exercises_solved"] += 1
    sessions = _sessions(db)
    for sid, secs in timecalc.seconds_by_key(sessions, lambda s: s.skill_id, None, n).items():
        if sid:
            agg[sid]["seconds"] = secs
    for s in sessions:
        if s.skill_id:
            a = agg[s.skill_id]
            if a["last_studied_at"] is None or s.started_at > a["last_studied_at"]:
                a["last_studied_at"] = s.started_at
    return agg


def skill_detail_stats(db: Session, skill: Skill) -> dict[str, Any]:
    n = now()
    overview = skill_overview(db).get(skill.id, {})
    sessions = [s for s in _sessions(db) if s.skill_id == skill.id]
    monthly: dict[str, int] = defaultdict(int)
    for s in sessions:
        for d, part in timecalc.split_by_day(*timecalc.session_interval(s, n)):
            monthly[_month_key(d)] += int((part[1] - part[0]).total_seconds())
    proficiency_history = [
        {"at": e.occurred_at.isoformat(), "old": e.old_value, "new": e.new_value}
        for e in db.scalars(
            select(HistoryEvent).where(
                HistoryEvent.entity_type == "skill", HistoryEvent.entity_id == skill.id,
                HistoryEvent.field == "proficiency",
            ).order_by(HistoryEvent.occurred_at)
        )
    ]
    reviews = list(db.scalars(select(ReviewLog).where(ReviewLog.skill_id == skill.id)))
    attempts = list(db.execute(
        select(ExerciseAttempt.outcome).join(Exercise).where(Exercise.skill_id == skill.id)
    ).scalars())
    quiz = list(db.scalars(select(AssessmentAttempt).where(AssessmentAttempt.skill_id == skill.id)))
    return {
        **overview,
        "time_by_month": [{"month": k, "hours": round(v / 3600, 2)} for k, v in sorted(monthly.items())],
        "proficiency_history": proficiency_history,
        "reviews_total": len(reviews),
        "reviews_remembered": sum(1 for r in reviews if r.remembered),
        "attempts_total": len(attempts),
        "attempts_solved": sum(1 for o in attempts if o in SOLVED_OUTCOMES),
        "quiz_avg_pct": round(sum(q.score_pct for q in quiz) / len(quiz), 1) if quiz else None,
    }


# --------------------------------------------------------------------------- roadmap


def roadmap_progress(db: Session) -> dict[str, Any]:
    items = list(db.scalars(select(RoadmapItem)))
    per_track: dict[str, dict[str, int]] = defaultdict(lambda: {"steps": 0, "done": 0, "items": 0, "items_completed": 0})
    total = done = 0
    for item in items:
        if item.status == "skipped":
            continue
        t = per_track[item.track]
        t["items"] += 1
        t["items_completed"] += item.status == "completed"
        for step in item.steps:
            t["steps"] += 1
            total += 1
            if step.done:
                t["done"] += 1
                done += 1
    return {
        "percent": round(100 * done / total, 1) if total else 0.0,
        "steps_total": total,
        "steps_done": done,
        "tracks": [{"track": k, **v, "percent": round(100 * v["done"] / v["steps"], 1) if v["steps"] else 0.0}
                   for k, v in per_track.items()],
    }


# --------------------------------------------------------------------------- dashboard


def dashboard(db: Session) -> dict[str, Any]:
    n = now()
    today = date.today()
    skills = list(db.scalars(select(Skill).where(Skill.is_archived.is_(False))))
    overview = skill_overview(db)
    topics = list(db.scalars(select(Topic)))
    topic_status = Counter(t.status for t in topics)
    ex_status = _exercise_status(db)
    sessions = _sessions(db)

    needs_improvement = [
        s for s in skills
        if overview.get(s.id, {}).get("topics_attention", 0) > 0
        or s.classification == "needs_revision"
    ]
    due = db.scalar(select(func.count()).select_from(ReviewItem).where(
        ReviewItem.is_active.is_(True), ReviewItem.due_date <= today))
    overdue = db.scalar(select(func.count()).select_from(ReviewItem).where(
        ReviewItem.is_active.is_(True), ReviewItem.due_date < today))
    revised_topics = db.scalar(select(func.count(func.distinct(ReviewLog.topic_id))).where(ReviewLog.topic_id.is_not(None)))

    # last 7 days and last 12 weeks of time
    last7 = timecalc.seconds_by_day(sessions, today - timedelta(days=6), today, n)
    w_start = _week_start(today) - timedelta(weeks=11)
    by_day_12w = timecalc.seconds_by_day(sessions, w_start, today, n)
    weekly: dict[date, int] = defaultdict(int)
    for d, secs in by_day_12w.items():
        weekly[_week_start(d)] += secs
    month_start = _months_ago(today, 11)
    by_day_12m = timecalc.seconds_by_day(sessions, month_start, today, n)
    monthly: dict[str, int] = defaultdict(int)
    for d, secs in by_day_12m.items():
        monthly[_month_key(d)] += secs
    months = []
    cur = month_start
    while cur <= today:
        months.append(_month_key(cur))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)

    skill_names = {s.id: s.name for s in skills}
    recent_topics = []
    for e in db.scalars(select(HistoryEvent).where(HistoryEvent.topic_id.is_not(None))
                        .order_by(HistoryEvent.occurred_at.desc()).limit(60)):
        if e.topic_id not in {r["id"] for r in recent_topics}:
            t = db.get(Topic, e.topic_id)
            if t:
                recent_topics.append({"id": t.id, "title": t.title, "status": t.status, "at": e.occurred_at.isoformat()})
        if len(recent_topics) >= 6:
            break

    pending_reviews = [
        _review_item_brief(db, r) for r in db.scalars(
            select(ReviewItem).where(ReviewItem.is_active.is_(True), ReviewItem.due_date <= today + timedelta(days=3))
            .order_by(ReviewItem.due_date).limit(8))
    ]
    goals = list(db.scalars(select(Goal).where(Goal.status.in_(["not_started", "in_progress"]))
                            .order_by(Goal.target_date.is_(None), Goal.target_date).limit(5)))
    attempted = len(ex_status)
    solved = sum(1 for v in ex_status.values() if v == "solved")
    return {
        "generated_at": n.isoformat(),
        "tracking_started_on": tracking_start(db).isoformat(),
        "skills": {
            "total": len(skills),
            "advanced": sum(1 for s in skills if (s.proficiency or 0) >= 5),
            "demonstrated": sum(1 for s in skills if s.classification == "demonstrated"),
            "learning": sum(1 for s in skills if s.status == "learning"),
            "to_learn": sum(1 for s in skills if s.classification in {"to_learn", "not_demonstrated"}),
            "needs_revision": sum(1 for s in skills if s.classification == "needs_revision"),
            "needs_improvement": len(needs_improvement),
            "not_assessed": sum(1 for s in skills if s.proficiency is None),
            "learning_list": [{"id": s.id, "name": s.name, "proficiency": s.proficiency} for s in skills if s.status == "learning"][:12],
            "needs_improvement_list": [{"id": s.id, "name": s.name} for s in needs_improvement][:12],
        },
        "topics": {
            "total": len(topics),
            "learned": sum(topic_status[s] for s in TOPIC_DONE_STATUSES),
            "by_status": dict(topic_status),
            "revised": revised_topics or 0,
            "pending_revision": due or 0,
            "overdue_revision": overdue or 0,
        },
        "exercises": {
            "total": db.scalar(select(func.count()).select_from(Exercise)) or 0,
            "attempted": attempted,
            "solved": solved,
        },
        "projects": {
            "total": db.scalar(select(func.count()).select_from(Project)) or 0,
            "completed": db.scalar(select(func.count()).select_from(Project).where(Project.status == "completed")) or 0,
            "active": db.scalar(select(func.count()).select_from(Project).where(Project.status == "active")) or 0,
        },
        "time": {
            "total_hours": round(timecalc.total_seconds(timecalc.session_interval(s, n) for s in sessions) / 3600, 2),
            "last7": [{"date": d.isoformat(), "minutes": round(last7.get(d, 0) / 60)} for d in _days(today - timedelta(days=6), today)],
            "weekly": [{"week": (w_start + timedelta(weeks=i)).isoformat(),
                        "hours": round(weekly.get(w_start + timedelta(weeks=i), 0) / 3600, 2)} for i in range(12)],
            "monthly": [{"month": m, "hours": round(monthly.get(m, 0) / 3600, 2)} for m in months],
        },
        "streak": streaks(db),
        "roadmap": roadmap_progress(db),
        "recent_topics": recent_topics,
        "pending_reviews": pending_reviews,
        "upcoming_goals": [{"id": g.id, "title": g.title, "target_date": g.target_date.isoformat() if g.target_date else None,
                            "priority": g.priority} for g in goals],
        "skill_names": skill_names,
    }


def _review_item_brief(db: Session, r: ReviewItem) -> dict[str, Any]:
    from ..models import Flashcard, InterviewQuestion
    title = ""
    if r.item_type == "topic":
        t = db.get(Topic, r.item_id)
        title = t.title if t else "(deleted topic)"
    elif r.item_type == "flashcard":
        c = db.get(Flashcard, r.item_id)
        title = c.front if c else "(deleted card)"
    elif r.item_type == "interview":
        q = db.get(InterviewQuestion, r.item_id)
        title = q.question if q else "(deleted question)"
    elif r.item_type == "question":
        from ..models import Question
        q = db.get(Question, r.item_id)
        title = q.prompt if q else "(deleted question)"
    return {"id": r.id, "item_type": r.item_type, "item_id": r.item_id, "title": title[:140],
            "due_date": r.due_date.isoformat(), "stage": r.stage, "lapses": r.lapses}


# --------------------------------------------------------------------------- day summary


def day_summary(db: Session, day: date) -> dict[str, Any]:
    n = now()
    w = _window(day, day)
    sessions = [s for s in _sessions(db, *w)]
    by_kind: dict[str, list] = defaultdict(list)
    by_skill: dict[Any, list] = defaultdict(list)
    for s in sessions:
        iv = timecalc.clip(*timecalc.session_interval(s, n), *w)
        if iv:
            by_kind[s.kind].append(iv)
            by_skill[s.skill_id].append(iv)
    all_iv = [iv for ivs in by_kind.values() for iv in ivs]
    reviews = list(db.scalars(select(ReviewLog).where(ReviewLog.reviewed_at >= w[0], ReviewLog.reviewed_at < w[1])))
    attempts = list(db.scalars(select(ExerciseAttempt).where(
        ExerciseAttempt.submitted_at >= w[0], ExerciseAttempt.submitted_at < w[1])))
    quizzes = list(db.scalars(select(AssessmentAttempt).where(AssessmentAttempt.taken_at >= w[0], AssessmentAttempt.taken_at < w[1])))
    events = list(db.scalars(select(HistoryEvent).where(
        HistoryEvent.occurred_at >= w[0], HistoryEvent.occurred_at < w[1]).order_by(HistoryEvent.occurred_at)))
    log = db.scalar(select(DailyLog).where(DailyLog.log_date == day))
    skill_names = dict(db.execute(select(Skill.id, Skill.name)).all())
    topic_names = dict(db.execute(select(Topic.id, Topic.title)).all())
    ex_titles = dict(db.execute(select(Exercise.id, Exercise.title)).all())
    return {
        "date": day.isoformat(),
        "total_minutes": round(timecalc.total_seconds(all_iv) / 60),
        "minutes_by_kind": {k: round(timecalc.total_seconds(v) / 60) for k, v in by_kind.items()},
        "minutes_by_skill": sorted(
            [{"skill_id": k, "skill": skill_names.get(k, "Unassigned"), "minutes": round(timecalc.total_seconds(v) / 60)}
             for k, v in by_skill.items()], key=lambda x: -x["minutes"]),
        "sessions": [{
            "id": s.id, "kind": s.kind, "title": s.title, "skill": skill_names.get(s.skill_id),
            "topic": topic_names.get(s.topic_id), "started_at": s.started_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None, "notes": s.notes, "problems": s.problems,
        } for s in sorted(sessions, key=lambda s: s.started_at)],
        "topics_studied": sorted({topic_names[s.topic_id] for s in sessions if s.topic_id in topic_names}),
        "reviews": {"total": len(reviews), "remembered": sum(r.remembered for r in reviews),
                    "by_rating": dict(Counter(r.rating for r in reviews))},
        "exercises": {
            "attempted": len({a.exercise_id for a in attempts}),
            "completed": len({a.exercise_id for a in attempts if a.outcome in SOLVED_OUTCOMES}),
            "items": [{"exercise_id": a.exercise_id, "title": ex_titles.get(a.exercise_id, "?"), "outcome": a.outcome,
                       "at": a.submitted_at.isoformat()} for a in attempts],
        },
        "quizzes": [{"mode": q.mode, "score_pct": q.score_pct, "passed": q.passed, "total": q.total, "topic": topic_names.get(q.topic_id),
                     "skill": skill_names.get(q.skill_id), "at": q.taken_at.isoformat()} for q in quizzes],
        "events": [{"at": e.occurred_at.isoformat(), "type": e.event_type, "summary": e.summary} for e in events],
        "log": None if not log else {
            "studied_md": log.studied_md, "accomplished_md": log.accomplished_md,
            "problems_md": log.problems_md, "tomorrow_md": log.tomorrow_md, "energy": log.energy,
        },
    }


# --------------------------------------------------------------------------- period totals (reports, weekly)


def period_totals(db: Session, start: date, end: date) -> dict[str, Any]:
    n = now()
    w = _window(start, end)
    sessions = _sessions(db, *w)
    secs = timecalc.total_seconds(
        iv for s in sessions if (iv := timecalc.clip(*timecalc.session_interval(s, n), *w)))
    reviews = list(db.scalars(select(ReviewLog).where(ReviewLog.reviewed_at >= w[0], ReviewLog.reviewed_at < w[1])))
    attempts = list(db.scalars(select(ExerciseAttempt).where(
        ExerciseAttempt.submitted_at >= w[0], ExerciseAttempt.submitted_at < w[1])))
    learned = list(db.scalars(select(Topic).where(Topic.completed_at >= w[0], Topic.completed_at < w[1])))
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "hours": round(secs / 3600, 2),
        "reviews": len(reviews),
        "reviews_remembered": sum(r.remembered for r in reviews),
        "exercises_attempted": len({a.exercise_id for a in attempts}),
        "exercises_solved": len({a.exercise_id for a in attempts if a.outcome in SOLVED_OUTCOMES}),
        "topics_learned": len(learned),
        "active_days": len({d for d in active_days(db, start) if start <= d <= end}),
    }


# --------------------------------------------------------------------------- analytics


def analytics(db: Session, start: date, end: date) -> dict[str, Any]:
    n = now()
    w = _window(start, end)
    days = _days(start, end)
    sessions = _sessions(db, *w)
    skills = {s.id: s for s in db.scalars(select(Skill))}
    by_day = timecalc.seconds_by_day(sessions, start, end, n)
    reviews = list(db.scalars(select(ReviewLog).where(ReviewLog.reviewed_at >= w[0], ReviewLog.reviewed_at < w[1])))
    attempts = list(db.scalars(select(ExerciseAttempt).where(
        ExerciseAttempt.submitted_at >= w[0], ExerciseAttempt.submitted_at < w[1])))
    exercises = {e.id: e for e in db.scalars(select(Exercise))}
    topics = list(db.scalars(select(Topic)))

    rev_by_day = Counter(r.reviewed_at.date() for r in reviews)
    att_by_day = Counter(a.submitted_at.date() for a in attempts)
    solved_by_day = Counter(a.submitted_at.date() for a in attempts if a.outcome in SOLVED_OUTCOMES)
    daily = [{"date": d.isoformat(), "minutes": round(by_day.get(d, 0) / 60), "reviews": rev_by_day.get(d, 0),
              "attempts": att_by_day.get(d, 0), "solved": solved_by_day.get(d, 0)} for d in days]

    weekly: dict[date, int] = defaultdict(int)
    for d in days:
        weekly[_week_start(d)] += by_day.get(d, 0)
    weekly_rev: dict[date, list[bool]] = defaultdict(list)
    for r in reviews:
        weekly_rev[_week_start(r.reviewed_at.date())].append(r.remembered)

    monthly: dict[str, dict[str, float]] = defaultdict(lambda: {"hours": 0.0, "topics_learned": 0, "solved": 0, "reviews": 0})
    for d in days:
        monthly[_month_key(d)]["hours"] += by_day.get(d, 0) / 3600
    for t in topics:
        if t.completed_at and _in(t.completed_at, w):
            monthly[_month_key(t.completed_at)]["topics_learned"] += 1
    first_solve: dict[int, datetime] = {}
    for a in attempts:
        if a.outcome in SOLVED_OUTCOMES and (a.exercise_id not in first_solve or a.submitted_at < first_solve[a.exercise_id]):
            first_solve[a.exercise_id] = a.submitted_at
    for ts in first_solve.values():
        monthly[_month_key(ts)]["solved"] += 1
    for r in reviews:
        monthly[_month_key(r.reviewed_at)]["reviews"] += 1

    tech = timecalc.seconds_by_key(sessions, lambda s: s.skill_id, w, n)
    tech_rows = sorted(({"skill_id": k, "skill": skills[k].name if k in skills else "Unassigned",
                         "hours": round(v / 3600, 2)} for k, v in tech.items() if v > 0), key=lambda r: -r["hours"])

    status_by_cat: dict[str, Counter] = defaultdict(Counter)
    for t in topics:
        status_by_cat[t.category][t.status] += 1

    # coding: per difficulty and language (exercise-level outcomes within range)
    per_ex: dict[int, dict[str, Any]] = {}
    for a in attempts:
        st = per_ex.setdefault(a.exercise_id, {"attempts": 0, "solved": False, "fail": 0, "secs": []})
        st["attempts"] += 1
        if a.outcome in SOLVED_OUTCOMES:
            st["solved"] = True
            if a.duration_seconds:
                st["secs"].append(a.duration_seconds)
        else:
            st["fail"] += 1
    diff: dict[str, dict[str, int]] = defaultdict(lambda: {"attempted": 0, "solved": 0})
    lang: dict[str, dict[str, int]] = defaultdict(lambda: {"attempted": 0, "solved": 0})
    weak: Counter = Counter()
    for ex_id, st in per_ex.items():
        ex = exercises.get(ex_id)
        if not ex:
            continue
        diff[ex.difficulty]["attempted"] += 1
        diff[ex.difficulty]["solved"] += st["solved"]
        lang[ex.language]["attempted"] += 1
        lang[ex.language]["solved"] += st["solved"]
        if not st["solved"] or st["fail"] >= 2:
            for c in ex.concepts[:3]:
                weak[c] += 1
    solve_secs = [s for st in per_ex.values() for s in st["secs"]]

    active = active_days(db, start)
    heat = [{"date": d.isoformat(), "minutes": round(by_day.get(d, 0) / 60), "active": d in active} for d in days]

    proficiency = sorted(
        ({"skill_id": s.id, "skill": s.name, "category": s.category, "proficiency": s.proficiency,
          "target": s.target_proficiency} for s in skills.values() if s.proficiency is not None and not s.is_archived),
        key=lambda r: (-(r["proficiency"] or 0), r["skill"]))

    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "daily": daily,
        "weekly": [{"week": k.isoformat(), "hours": round(v / 3600, 2)} for k, v in sorted(weekly.items())],
        "monthly": [{"month": k, **{kk: round(vv, 2) for kk, vv in v.items()}} for k, v in sorted(monthly.items())],
        "technology_time": tech_rows,
        "proficiency": proficiency,
        "topic_status": [{"category": c, **dict(cnt)} for c, cnt in sorted(status_by_cat.items())],
        "topic_status_totals": dict(Counter(t.status for t in topics)),
        "revision": {
            "total": len(reviews),
            "remembered": sum(r.remembered for r in reviews),
            "by_rating": dict(Counter(r.rating for r in reviews)),
            "weekly": [{"week": k.isoformat(), "total": len(v), "rate": round(100 * sum(v) / len(v), 1)}
                       for k, v in sorted(weekly_rev.items())],
        },
        "coding": {
            "attempted": len(per_ex),
            "solved": sum(1 for st in per_ex.values() if st["solved"]),
            "failed_attempts": sum(st["fail"] for st in per_ex.values()),
            "avg_attempts": round(sum(st["attempts"] for st in per_ex.values()) / len(per_ex), 2) if per_ex else 0,
            "avg_solve_minutes": round(sum(solve_secs) / len(solve_secs) / 60, 1) if solve_secs else None,
            "by_difficulty": [{"difficulty": k, **v, "rate": round(100 * v["solved"] / v["attempted"], 1)}
                              for k, v in diff.items()],
            "by_language": [{"language": k, **v} for k, v in lang.items()],
            "weak_concepts": [{"concept": k, "count": v} for k, v in weak.most_common(8)],
            "calendar": [{"date": d.isoformat(), "attempts": att_by_day.get(d, 0), "solved": solved_by_day.get(d, 0)} for d in days],
        },
        "heatmap": heat,
        "roadmap": roadmap_progress(db),
        "totals": period_totals(db, start, end),
    }
