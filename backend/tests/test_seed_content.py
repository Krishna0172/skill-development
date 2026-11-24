"""Quality gate for the seeded reference content (v2).

- Every auto-checked exercise is solvable (reference solution passes) and not pre-solved (starter fails).
- Topics, topic extras, the curriculum and projects only reference things that exist.
- Every typed question is well formed, and every `output` question's expected output is what the code really prints.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.seed.loader import V1_SKILL_MAP
from app.services import runner
from app.services.assessment import normalise_output

CONTENT = Path(__file__).resolve().parent.parent / "app" / "seed" / "content"
EXERCISES = sorted((CONTENT / "exercises").glob("*.json"))
AUTO = [p for p in EXERCISES if json.loads(p.read_text(encoding="utf-8")).get("tests")]
TOPIC_FILES = sorted((CONTENT / "topics").glob("*.json"))
EXTRA_FILES = sorted((CONTENT / "topic_extras").glob("*.json"))


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _skills() -> set[str]:
    return {s["slug"] for s in _load(CONTENT / "skills.json")}


def _topics() -> set[str]:
    return {_load(p)["slug"] for p in TOPIC_FILES}


def _questions() -> list[tuple[str, int, dict]]:
    return [(p.name, i, q) for p in TOPIC_FILES + EXTRA_FILES for i, q in enumerate(_load(p).get("questions", []))]


OUTPUT_QS = [x for x in _questions() if x[2]["type"] == "output"]


@pytest.mark.parametrize("path", AUTO, ids=[p.stem for p in AUTO])
def test_reference_solution_passes_and_starter_fails(path: Path):
    ex = _load(path)
    if ex["language"] == "javascript" and not runner.node_available():
        pytest.skip("node not installed")
    good = runner.run(ex["language"], ex["solution_code"], ex["tests"])
    assert good["passed"], f"solution fails: {good.get('error')} {[r for r in good['results'] if not r.get('passed')][:2]}"
    bad = runner.run(ex["language"], ex["starter_code"], ex["tests"])
    assert not bad["passed"], "starter code already passes every test"


def test_topic_files_are_well_formed():
    skills = _skills()
    topics = _topics()
    for path in TOPIC_FILES:
        t = _load(path)
        assert t["slug"] == path.stem
        skill = V1_SKILL_MAP.get(t["skill"], t["skill"])
        assert skill in skills, f"{path.name}: unknown skill {t['skill']}"
        assert t.get("track") in (None, "python", "backend", "ai"), path.name
        for rel in t.get("related", []):
            # v1 JS/React topics were removed on purpose; the loader skips links to them
            assert rel in topics or rel.startswith(("javascript", "react")), f"{path.name}: unknown related topic {rel}"
    for path in EXTRA_FILES:
        assert _load(path)["slug"] in topics, f"{path.name}: topic extra for an unknown topic"


def test_questions_are_well_formed():
    qs = _questions()
    assert len(qs) > 300
    for name, i, q in qs:
        where = f"{name} q{i}"
        assert q.get("prompt", "").strip(), where
        t = q["type"]
        if t == "mcq":
            assert len(q["options"]) >= 2, where
            assert 0 <= q["answer_index"] < len(q["options"]), where
        elif t == "fill_blank":
            assert q["answers"] and all(str(a).strip() for a in q["answers"]), where
        elif t == "output":
            assert q.get("code", "").strip() and q["answers"], where
        elif t in ("debug", "explain", "qa"):
            assert q.get("answer", "").strip(), where
            assert isinstance(q.get("rubric", []), list), where
        else:
            pytest.fail(f"{where}: unknown question type {t}")


@pytest.mark.parametrize("name,idx,q", OUTPUT_QS, ids=[f"{n[:-5]}-{i}" for n, i, _ in OUTPUT_QS])
def test_output_questions_print_their_expected_output(name, idx, q):
    """Re-run each 'predict the output' snippet: the stored answer must be what Python really prints."""
    proc = subprocess.run([sys.executable, "-I", "-c", q["code"]], capture_output=True, text=True, timeout=15,
                          cwd=tempfile.gettempdir())
    assert proc.returncode == 0, proc.stderr[-500:]
    assert normalise_output(proc.stdout) in {normalise_output(a) for a in q["answers"]}, proc.stdout[:300]


def test_curriculum_references_known_topics_and_skills():
    topics, skills = _topics(), _skills()
    cur = _load(CONTENT / "curriculum.json")
    assert {t["slug"] for t in cur["tracks"]} == {"python", "backend", "ai"}
    for track in cur["tracks"]:
        for m in track["modules"]:
            for slug in m["topics"]:
                assert slug in topics, f"{track['slug']} module {m['n']}: unknown topic {slug}"
    for p in cur["projects"]:
        assert p["track"] in {"python", "backend", "ai"} and p["steps"], p["slug"]
    for p in _load(CONTENT / "projects.json"):
        assert set(p.get("skills", [])) <= skills, (p["slug"], set(p["skills"]) - skills)
        assert set(p.get("topics", [])) <= topics, (p["slug"], set(p["topics"]) - topics)

