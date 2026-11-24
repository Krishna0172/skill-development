"""Runner upgrades, additive migrations, SRS ease, weak-spot quiz, exercise editor, playground."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app import migrate
from app.db import Base
from app.services import assessment, revision, runner

TESTS = {"function": "f", "cases": [{"args": [2], "expected": 4}, {"args": [3], "expected": 9}]}


# ---- runner
def test_runner_passes_and_reports_time():
    r = runner.run("python", "def f(x):\n    return x * x\n", TESTS)
    assert r["passed"] and r["tests_passed"] == 2 and "ms" in r["results"][0]


def test_runner_hint_when_return_missing():
    r = runner.run("python", "def f(x):\n    x * x\n", TESTS)
    assert not r["passed"] and "return" in r["results"][0]["message"]


def test_runner_per_test_timeout_skips_rest():
    r = runner.run("python", "def f(x):\n    while True: pass\n", TESTS)
    assert not r["passed"] and "did not finish" in r["results"][0]["error"]
    assert r["results"][1]["got"].startswith("(skipped")


def test_runner_syntax_error_points_at_line():
    r = runner.run("python", "def f(x)\n    return x\n", TESTS)
    assert "SyntaxError" in r["error"] and "line 1" in r["error"]


def test_runner_traceback_shows_only_user_frames():
    r = runner.run("python", "def f(x):\n    return [][5]\n", TESTS)
    err = r["results"][0]["error"]
    assert "IndexError" in err and "line 2" in err and "harness" not in err


def test_runner_recursion_error_is_friendly():
    r = runner.run("python", "def f(x):\n    return f(x)\n", TESTS)
    assert "RecursionError" in r["results"][0]["error"]


def test_scratch_stdin_and_errors():
    assert runner.run_python_script("print(int(input()) * 2)", "21")["stdout"].strip() == "42"
    bad = runner.run_python_script("print(1/0)")
    assert "ZeroDivisionError" in bad["stderr"] and "scratch.py" not in bad["stderr"]
    assert "SyntaxError" in runner.run_python_script("def x(:\n")["stderr"]


# ---- migrations
def test_additive_migration_adds_missing_column(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("ALTER TABLE review_items DROP COLUMN ease"))
    assert [t for t, _col, _ in migrate.missing_columns(engine)] == ["review_items"]
    called = []
    done = migrate.apply_additive(engine, lambda: called.append(1))
    assert done == ["review_items.ease"] and called == [1]
    assert "ease" in {c["name"] for c in inspect(engine).get_columns("review_items")}
    assert migrate.apply_additive(engine, lambda: called.append(2)) == [] and called == [1]


# ---- SRS ease
def test_ease_scales_intervals_but_default_is_unchanged():
    assert revision.next_state(2, "easy").interval_days == 14
    hard = revision.next_state(2, "easy", ease=1.3)
    assert hard.interval_days < 14 and hard.ease == 1.4
    assert revision.next_state(2, "easy", ease=3.0).interval_days > 14
    assert revision.next_state(3, "forgot").ease == 2.3
    assert revision.next_state(3, "forgot", ease=1.3).ease == 1.3  # clamped at the floor


# ---- weak-spot quiz
def test_weak_questions_ranked_by_misses(client):
    from app.db import SessionLocal
    from app.models import AssessmentAttempt

    with SessionLocal() as db:
        a, b, c = [q for (q,) in db.execute(text("SELECT id FROM questions WHERE type='mcq' LIMIT 3"))]
        for details in ([{"question_id": a, "correct": False}, {"question_id": b, "correct": False}],
                        [{"question_id": a, "correct": False}, {"question_id": c, "correct": True}]):
            db.add(AssessmentAttempt(mode="quiz", total=2, score=0, score_pct=0, details=details))
        db.commit()
        assert assessment.weak_question_ids(db, 5) == [a, b]
    r = client.get("/revision/quiz?weak=1&limit=3")
    assert r.status_code == 200 and "Weak-spot" in r.text


# ---- pages
def test_playground_and_scratch_endpoints(client):
    assert client.get("/playground").status_code == 200
    r = client.post("/playground/run", data={"code": "print(input())", "stdin": "hi"})
    assert r.status_code == 200 and "hi" in r.text


def test_exercise_editor_roundtrip(client):
    assert client.get("/practice/new").status_code == 200
    tests = '{"function": "dbl", "cases": [{"args": [2], "expected": 4}]}'
    r = client.post("/practice/save", data={
        "title": "Double it", "category": "python-fundamentals", "language": "python", "difficulty": "easy",
        "problem_md": "Return 2x.", "starter_code": "def dbl(x):\n    pass", "solution_code": "def dbl(x):\n    return 2*x",
        "tests": tests}, follow_redirects=False)
    assert r.status_code == 303 and "passes" in r.headers["location"]
    eid = r.headers["location"].split("/practice/")[1].split("?")[0]
    assert client.get(f"/practice/{eid}/edit").status_code == 200
    run = client.post(f"/practice/{eid}/run", data={"code": "def dbl(x):\n    return x*2"}, headers={"HX-Request": "true"})
    assert "all tests passed" in run.text
    scratch = client.post(f"/practice/{eid}/scratch", data={"code": "print('dbg')"})
    assert "dbg" in scratch.text
    bad = client.post("/practice/save", data={"title": "x", "category": "sql", "language": "python", "difficulty": "easy",
                                              "problem_md": "p", "tests": "{nope"})
    assert bad.status_code == 422 and "invalid" in bad.text
