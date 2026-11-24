"""End-to-end API workflows: every section must feed the same tracking system."""

from datetime import date, datetime, timedelta

import pytest

H = timedelta(hours=1)


def _local(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _skill(client, name="Python Testing", **kw):
    r = client.post("/api/skills", json={"name": name, "category": "Testing", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _topic(client, skill_id, title="Fixtures", **kw):
    r = client.post("/api/topics", json={"title": title, "skill_id": skill_id, "category": "Testing", **kw})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- skills


def test_seeded_skills_have_no_invented_proficiency(client):
    skills = client.get("/api/skills").json()
    assert len(skills) > 40
    assert all(s["proficiency"] is None for s in skills), "the app must never guess proficiency"
    dash = client.get("/api/dashboard").json()
    assert dash["time"]["total_hours"] == 0
    assert dash["exercises"]["attempted"] == 0
    assert dash["streak"]["current"] == 0


def test_skill_level_changes_are_kept_as_history(empty_client):
    c = empty_client
    s = _skill(c)
    c.patch(f"/api/skills/{s['id']}", json={"proficiency": 2})
    c.patch(f"/api/skills/{s['id']}", json={"proficiency": 3, "change_note": "built fixtures"})
    detail = c.get(f"/api/skills/{s['id']}").json()
    assert detail["proficiency"] == 3
    hist = detail["detail"]["proficiency_history"]
    assert [(h["old"], h["new"]) for h in hist] == [(None, "2"), ("2", "3")]


def test_validation_rejects_bad_input(empty_client):
    assert empty_client.post("/api/skills", json={"name": "", "category": "X"}).status_code == 422
    s = _skill(empty_client)
    assert empty_client.patch(f"/api/skills/{s['id']}", json={"proficiency": 9}).status_code == 422
    assert empty_client.patch(f"/api/skills/{s['id']}", json={"unknown": 1}).status_code == 422
    assert empty_client.post("/api/topics", json={"title": "x", "skill_id": 9999}).status_code == 422


# --------------------------------------------------------------------------- topic → revision → roadmap


def test_learning_a_topic_schedules_revision_and_completes_roadmap_step(empty_client):
    c = empty_client
    s = _skill(c)
    t = _topic(c, s["id"])
    rm = c.post("/api/roadmap", json={"title": "Testing track", "track": "to_learn", "steps": ["read"]}).json()
    step_id = rm["steps"][0]["id"]
    c.patch(f"/api/roadmap-steps/{step_id}", json={"topic_id": t["id"]})

    t = c.patch(f"/api/topics/{t['id']}", json={"status": "learned"}).json()
    assert t["started_at"] and t["completed_at"]
    assert t["review"]["due_date"] == (date.today() + timedelta(days=1)).isoformat()  # Day 1

    roadmap = c.get("/api/roadmap").json()
    item = next(i for i in roadmap["items"] if i["id"] == rm["id"])
    assert item["steps"][0]["done"] is True
    assert item["status"] == "in_progress"
    assert roadmap["progress"]["percent"] == 100.0


def test_revision_rating_moves_schedule_and_topic_status(empty_client):
    c = empty_client
    s = _skill(c)
    t = _topic(c, s["id"], status="learned")
    item_id = t["review"]["id"]

    r = c.post(f"/api/revision/items/{item_id}/rate", json={"rating": "easy"}).json()
    assert r["interval_days"] == 3 and r["remembered"]

    r = c.post(f"/api/revision/items/{item_id}/rate", json={"rating": "forgot"}).json()
    assert r["interval_days"] == 1 and not r["remembered"]
    topic = c.get(f"/api/topics/{t['id']}").json()
    assert topic["status"] == "needs_revision"
    assert topic["review"]["lapses"] == 1
    assert [lg["rating"] for lg in topic["review_logs"]] == ["forgot", "easy"]

    stats = c.get("/api/revision/stats").json()
    assert stats["total_reviews"] == 2 and stats["success_rate"] == 50.0


def test_duplicate_review_submission_is_rejected(empty_client):
    c = empty_client
    t = _topic(c, _skill(c)["id"], status="learned")
    body = {"rating": "easy", "client_ref": "abc12345-dup"}
    assert c.post(f"/api/revision/items/{t['review']['id']}/rate", json=body).status_code == 200
    assert c.post(f"/api/revision/items/{t['review']['id']}/rate", json=body).status_code == 409


# --------------------------------------------------------------------------- time tracking


def test_manual_sessions_reject_overlap_unless_allowed_and_never_double_count(empty_client):
    c = empty_client
    s = _skill(c)
    start = datetime.now().replace(microsecond=0) - 5 * H
    first = {"skill_id": s["id"], "started_at": _local(start), "ended_at": _local(start + H)}
    assert c.post("/api/sessions", json=first).status_code == 201

    overlap = {"skill_id": s["id"], "started_at": _local(start + H / 2), "ended_at": _local(start + 2 * H)}
    r = c.post("/api/sessions", json=overlap)
    assert r.status_code == 409 and r.json()["detail"]["overlaps"]

    assert c.post("/api/sessions", json={**overlap, "allow_overlap": True}).status_code == 201
    day = c.get(f"/api/activity/day/{start.date().isoformat()}").json()
    if (start + 2 * H).date() == start.date():
        assert day["total_minutes"] == 120  # 1h + 1.5h overlapping by 0.5h = 2h, not 2.5h


def test_timer_start_stop_and_single_running_timer(empty_client):
    c = empty_client
    t1 = c.post("/api/timer/start", json={"kind": "learning", "title": "first"}).json()
    t2 = c.post("/api/timer/start", json={"kind": "practice", "title": "second"}).json()
    sessions = c.get("/api/sessions").json()
    running = [s for s in sessions if s["running"]]
    assert len(running) == 1 and running[0]["id"] == t2["id"]
    assert next(s for s in sessions if s["id"] == t1["id"])["ended_at"] is not None
    stopped = c.post("/api/timer/stop", json={"notes": "done"}).json()
    assert stopped["running"] is False and stopped["notes"] == "done"
    assert c.post("/api/timer/stop").status_code == 404


def test_session_rejects_future_and_inverted_times(empty_client):
    now = datetime.now().replace(microsecond=0)
    bad = {"started_at": _local(now), "ended_at": _local(now - H)}
    assert empty_client.post("/api/sessions", json=bad).status_code == 422
    future = {"started_at": _local(now + 2 * H), "ended_at": _local(now + 3 * H)}
    assert empty_client.post("/api/sessions", json=future).status_code == 422


def test_daily_log_upsert_and_day_summary(empty_client):
    today = date.today().isoformat()
    r = empty_client.put(f"/api/daily-logs/{today}", json={"studied_md": "pytest fixtures"})
    assert r.status_code == 200
    r = empty_client.put(f"/api/daily-logs/{today}", json={"studied_md": "pytest fixtures + mocks", "energy": 4})
    assert len(empty_client.get("/api/daily-logs").json()) == 1
    assert empty_client.get(f"/api/activity/day/{today}").json()["log"]["energy"] == 4


# --------------------------------------------------------------------------- practice


@pytest.fixture
def py_exercise(empty_client):
    body = {
        "title": "Add", "category": "python-fundamentals", "language": "python", "difficulty": "easy",
        "problem_md": "add(a, b)", "starter_code": "def add(a, b):\n    ...\n",
        "solution_code": "def add(a, b):\n    return a + b\n", "solution_md": "use +",
        "tests": {"function": "add", "cases": [{"args": [1, 2], "expected": 3}, {"args": [-1, 1], "expected": 0},
                                               {"args": ["a", 1], "raises": "TypeError"}]},
    }
    r = empty_client.post("/api/exercises", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_exercise_hides_solution_until_attempted(empty_client, py_exercise):
    c, ex = empty_client, py_exercise
    assert "solution_code" not in ex and "tests" not in ex
    assert c.get(f"/api/exercises/{ex['id']}/solution").status_code == 403
    c.post(f"/api/exercises/{ex['id']}/attempts", json={"code": "def add(a, b):\n    return a - b\n"})
    sol = c.get(f"/api/exercises/{ex['id']}/solution")
    assert sol.status_code == 200 and "a + b" in sol.json()["solution_code"]


def test_attempts_are_auto_checked_and_tracked(empty_client, py_exercise):
    c, ex = empty_client, py_exercise
    bad = c.post(f"/api/exercises/{ex['id']}/attempts", json={"code": "def add(a, b):\n    return a - b\n"}).json()
    assert bad["attempt"]["outcome"] == "failed" and bad["status"] == "attempted"
    good = c.post(f"/api/exercises/{ex['id']}/attempts", json={"code": "def add(a, b):\n    return a + b\n"}).json()
    assert good["attempt"]["outcome"] == "passed" and good["first_solve"] and good["status"] == "solved"
    loop = c.post(f"/api/exercises/{ex['id']}/run", json={"code": "def add(a, b):\n    while True: pass\n"}).json()
    assert loop["passed"] is False and "Timed out" in loop["error"]
    dash = c.get("/api/dashboard").json()
    assert dash["exercises"] == {"total": 1, "attempted": 1, "solved": 1}


def test_sql_runner_checks_rows():
    from app.services import runner
    tests = {"schema_sql": "CREATE TABLE t(a INT, b REAL); INSERT INTO t VALUES (1, 2.0),(2, 3.5);",
             "ordered": True, "expected": {"columns": ["a", "b"], "rows": [[1, 2], [2, 3.5]]}}
    assert runner.run("sql", "SELECT a, b FROM t ORDER BY a;", tests)["passed"]
    assert not runner.run("sql", "SELECT a, b FROM t ORDER BY a DESC", tests)["passed"]
    assert runner.run("sql", "SELEC nope", tests)["error"].startswith("SQL error")


# --------------------------------------------------------------------------- goals


def test_goal_progress_is_computed_from_records(empty_client, py_exercise):
    c = empty_client
    g = c.post("/api/goals", json={"title": "Solve 2 python exercises", "metric": "exercises_solved",
                                   "metric_target": 2, "metric_filter": {"category": "python-fundamentals"},
                                   "target_date": (date.today() - timedelta(days=1)).isoformat()}).json()
    assert g["progress"]["current"] == 0 and g["progress"]["overdue"] is True
    c.post(f"/api/exercises/{py_exercise['id']}/attempts", json={"code": "def add(a, b):\n    return a + b\n"})
    g = next(x for x in c.get("/api/goals").json() if x["id"] == g["id"])
    assert g["progress"]["current"] == 1 and g["progress"]["percent"] == 50.0
    assert c.post("/api/goals", json={"title": "x", "metric": "hours"}).status_code == 422


# --------------------------------------------------------------------------- data safety


def test_export_import_roundtrip_and_backup_restore(empty_client):
    c = empty_client
    s = _skill(c, name="Roundtrip")
    c.patch(f"/api/skills/{s['id']}", json={"proficiency": 3})
    export = c.get("/api/data/export")
    assert export.status_code == 200

    backup = c.post("/api/backups").json()["name"]
    c.patch(f"/api/skills/{s['id']}", json={"proficiency": 5})

    restored = c.post(f"/api/backups/{backup}/restore")
    assert restored.status_code == 200, restored.text
    assert c.get(f"/api/skills/{s['id']}").json()["proficiency"] == 3

    c.patch(f"/api/skills/{s['id']}", json={"proficiency": 1})
    r = c.post("/api/data/import", files={"file": ("e.json", export.content, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["safety_backup"]
    assert c.get(f"/api/skills/{s['id']}").json()["proficiency"] == 3

    bad = c.post("/api/data/import", files={"file": ("x.json", b'{"format": "nope"}', "application/json")})
    assert bad.status_code == 422
    assert c.get(f"/api/skills/{s['id']}").json()["proficiency"] == 3  # unchanged after a rejected import


def test_seed_never_readds_deleted_items(settings_for):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings_for(seed=True))) as c:
        projects = c.get("/api/projects").json()
        victim = next(p for p in projects if p["slug"] == "jpg-image-conversion")
        assert c.delete(f"/api/projects/{victim['id']}").status_code == 204
    with TestClient(create_app(settings_for(seed=True))) as c:  # restart → seeding runs again
        assert all(p["slug"] != "jpg-image-conversion" for p in c.get("/api/projects").json())


def test_search_finds_across_types(client):
    res = client.get("/api/search", params={"q": "upsert"}).json()["results"]
    types = {r["type"] for r in res}
    assert "project" in types or "topic" in types
    assert client.get("/api/search", params={"q": "   "}).json()["count"] == 0


def test_reports_and_analytics_shapes(client):
    for rng in ("7d", "30d", "90d", "180d", "365d", "all"):
        assert client.get("/api/analytics", params={"range": rng}).status_code == 200
    assert client.get("/api/analytics", params={"range": "custom", "start": "2026-01-02", "end": "2026-01-01"}).status_code == 422
    csv = client.get("/api/reports/weekly", params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert client.get("/api/reports/monthly", params={"month": "2026-13"}).status_code == 422
