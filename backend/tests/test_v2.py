"""v2 behaviour: 1–6 proficiency scale, typed assessments, the mastery gate, targets, and the server-rendered pages."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from app.db import SessionLocal
from app.enums import EXERCISE_CATEGORY_LABELS
from app.services import assessment, progress


def _skill(c, name="Testing"):
    r = c.post("/api/skills", json={"name": name, "category": "Testing"})
    assert r.status_code == 201, r.text
    return r.json()


def _topic_with_questions(c):
    s = _skill(c)
    t = c.post("/api/topics", json={"title": "Generators", "skill_id": s["id"], "category": "Python"}).json()
    qs = [
        {"type": "mcq", "prompt": "yield makes a…", "options": ["class", "generator"], "answer_index": 1},
        {"type": "fill_blank", "prompt": "next(g) on an exhausted generator raises ____", "answers": ["StopIteration"]},
        {"type": "output", "prompt": "What prints?", "code": "print(list(range(3)))", "answers": ["[0, 1, 2]"]},
        {"type": "explain", "prompt": "Why are generators lazy?", "answer_md": "They compute on demand.",
         "rubric": ["on demand", "memory"]},
    ]
    ids = []
    for q in qs:
        r = c.post("/api/questions", json={"topic_id": t["id"], **q})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return t, ids


def _submit(c, topic_id, ids, answers, ticks=()):
    responses = [{"question_id": i, "response": a} for i, a in zip(ids, answers)]
    responses[3]["rubric_ticks"] = list(ticks)
    r = c.post("/api/assessments", json={"topic_id": topic_id, "responses": responses})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- 1–6 scale


def test_proficiency_uses_one_to_six_scale(empty_client):
    c = empty_client
    s = _skill(c)
    assert c.patch(f"/api/skills/{s['id']}", json={"proficiency": 6, "target_proficiency": 6}).status_code == 200
    for bad in (0, 7):
        assert c.patch(f"/api/skills/{s['id']}", json={"proficiency": bad}).status_code == 422
        assert c.patch(f"/api/skills/{s['id']}", json={"target_proficiency": bad}).status_code == 422
    assert c.patch(f"/api/skills/{s['id']}", json={"classification": "guessing"}).status_code == 422


def test_v1_quiz_endpoints_are_gone_and_mixed_quiz_is_auto_graded_only(client):
    topic_id = client.get("/api/topics").json()[0]["id"]
    assert client.get(f"/api/topics/{topic_id}/quiz").status_code in (404, 405)
    assert client.post("/api/quiz/attempts", json={}).status_code in (404, 405)
    qs = client.get("/api/quiz/questions?limit=20").json()
    assert 0 < len(qs) <= 20
    assert {q["type"] for q in qs} <= {"mcq", "fill_blank", "output"}
    assert all("answer_index" not in q and "answers" not in q for q in qs), "quiz must not leak answers"


# --------------------------------------------------------------------------- grading


def test_output_normalisation_ignores_trailing_whitespace_only():
    assert assessment.normalise_output("a  \r\nb\n\n") == "a\nb"
    assert assessment.normalise_output(" a") != assessment.normalise_output("a")


def test_assessment_grades_every_question_type(empty_client):
    c = empty_client
    t, ids = _topic_with_questions(c)
    r = _submit(c, t["id"], ids, ["1", "  stopiteration ", "[0, 1, 2]\n", ""], ticks=[0])
    by_type = {i["type"]: i for i in r["items"]}
    assert by_type["mcq"]["correct"] and by_type["fill_blank"]["correct"] and by_type["output"]["correct"]
    assert by_type["explain"]["points"] == 0.5 and by_type["explain"]["correct"] is None
    assert r["score_pct"] == 87.5 and r["passed"]
    assert r["items"][0]["question"]["answer_index"] == 1, "answers are revealed after grading"


def test_missed_auto_graded_questions_are_scheduled_for_revision(empty_client):
    c = empty_client
    t, ids = _topic_with_questions(c)
    _submit(c, t["id"], ids, ["0", "ValueError", "[1, 2, 3]", ""])
    queue = c.get("/api/revision/queue?days_ahead=2").json()
    scheduled = {i["item_id"] for k in ("overdue", "due_today", "upcoming") for i in queue[k] if i["item_type"] == "question"}
    assert scheduled == set(ids[:3])


def test_topic_assessment_rejects_questions_from_another_topic(empty_client):
    c = empty_client
    t1, ids1 = _topic_with_questions(c)
    t2 = c.post("/api/topics", json={"title": "Other", "category": "Python"}).json()
    r = c.post("/api/assessments", json={"topic_id": t2["id"], "responses": [{"question_id": ids1[0], "response": "1"}]})
    assert r.status_code == 422


# --------------------------------------------------------------------------- mastery gate


def test_learned_and_mastered_need_evidence(empty_client):
    c = empty_client
    t, ids = _topic_with_questions(c)
    r = c.patch(f"/api/topics/{t['id']}", json={"status": "learned"})
    assert r.status_code == 422 and "assessment" in r.json()["detail"]

    failed = _submit(c, t["id"], ids, ["0", "", "", ""])
    assert not failed["passed"]
    assert c.get(f"/api/topics/{t['id']}").json()["status"] == "learning"

    passed = _submit(c, t["id"], ids, ["1", "StopIteration", "[0, 1, 2]", ""], ticks=[0, 1])
    assert passed["score_pct"] == 100.0
    topic = c.get(f"/api/topics/{t['id']}").json()
    assert topic["status"] == "learned" and topic["best_score"] == 100.0

    r = c.patch(f"/api/topics/{t['id']}", json={"status": "mastered"})
    assert r.status_code == 422 and "revision" in r.json()["detail"], "a high score alone is not mastery"


def test_topic_without_questions_can_be_marked_learned(empty_client):
    c = empty_client
    t = c.post("/api/topics", json={"title": "My own note", "category": "General"}).json()
    assert c.patch(f"/api/topics/{t['id']}", json={"status": "learned"}).status_code == 200


# --------------------------------------------------------------------------- targets


def test_target_progress_is_computed_from_sessions(empty_client):
    c = empty_client
    assert c.post("/targets", data={"period": "daily", "metric": "minutes", "value": "60"}).status_code == 200
    end = datetime.now().replace(microsecond=0) - timedelta(minutes=1)
    r = c.post("/api/sessions", json={"kind": "learning", "title": "t", "started_at": (end - timedelta(minutes=30)).isoformat(),
                                      "ended_at": end.isoformat()})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        rows = [t for t in progress.targets_progress(db) if t["period"] == "daily" and t["metric"] == "minutes"]
    if end.date() != datetime.now().date():
        pytest.skip("ran across midnight")
    assert len(rows) == 1 and rows[0]["target"] == 60 and rows[0]["current"] == pytest.approx(30, abs=1)
    assert rows[0]["percent"] == pytest.approx(50, abs=2)

    # same period + metric updates the existing target instead of adding another
    c.post("/targets", data={"period": "daily", "metric": "minutes", "value": "90"})
    with SessionLocal() as db:
        rows = [t for t in progress.targets_progress(db) if t["period"] == "daily" and t["metric"] == "minutes"]
    assert len(rows) == 1 and rows[0]["target"] == 90
    assert "err=" in str(c.post("/targets", data={"period": "yearly", "metric": "minutes", "value": "5"}).url)


def test_seeded_exercises_use_v2_categories_and_no_javascript(client):
    exercises = client.get("/api/exercises").json()
    assert len(exercises) > 50
    assert {e["category"] for e in exercises} <= set(EXERCISE_CATEGORY_LABELS)
    assert all(e["language"] != "javascript" for e in exercises)


# --------------------------------------------------------------------------- pages

STATIC_PAGES = [
    "/", "/dashboard", "/search?q=index", "/learn/python", "/learn/backend", "/learn/ai", "/topics", "/topics/new",
    "/revision", "/revision/session", "/revision/quiz", "/practice", "/interview", "/interview/mock", "/claims",
    "/skills", "/skills/assess", "/projects", "/targets", "/tracker", "/analytics", "/analytics?range=all",
    "/reports", "/reports?kind=monthly", "/settings",
]


def test_every_page_returns_200(client):
    for url in STATIC_PAGES:
        r = client.get(url)
        assert r.status_code == 200, f"{url}: {r.status_code}"
    # one detail page of each kind, picked from the list pages
    for list_url, pattern in [("/topics", r'href="(/topics/\d+)"'), ("/practice", r'href="(/practice/\d+)"'),
                              ("/skills", r'href="(/skills/\d+)"'), ("/projects", r'href="(/projects/\d+)"')]:
        detail = re.search(pattern, client.get(list_url).text).group(1)
        for url in (detail, detail + "?edit=1" if "/topics/" not in detail else detail + "/edit"):
            assert client.get(url).status_code == 200, url
    topic = re.search(r'href="/topics/(\d+)"', client.get("/topics").text).group(1)
    assert client.get(f"/topics/{topic}/assess").status_code == 200


def test_pages_redirect_with_errors_instead_of_crashing(client):
    r = client.get("/analytics?start=2026-05-01&end=2026-01-01")
    assert r.status_code == 200 and "err=" in str(r.url)
    r = client.post("/skills/new", data={"name": ""})
    assert r.status_code == 200 and "err=" in str(r.url)
    assert client.get("/topics/999999").status_code == 404


def test_topic_scratch_run(client):
    topic = re.search(r'href="/topics/(\d+)"', client.get("/topics").text).group(1)
    url = f"/topics/{topic}/scratch/run"
    assert "2" in client.post(url, data={"code": "print(1 + 1)"}).text
    assert "SyntaxError" in client.post(url, data={"code": "def ("}).text
    assert "Timed out" in client.post(url, data={"code": "while True: pass"}).text
    assert f"/topics/{topic}/scratch/run" in client.get(f"/topics/{topic}").text
