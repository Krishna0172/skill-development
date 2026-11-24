"""Executes practice-exercise code against test cases.

Security note: this is a single-user app bound to 127.0.0.1 that runs YOUR OWN code on YOUR
OWN machine — the same trust level as running a script in a terminal. Python/JS still run in
a separate process (isolated mode, temp working dir, hard timeout) so a bad loop can't hang
the server. SQL runs against a fresh in-memory SQLite database each time.

Comparison rules are documented in the exercise seed format and mirrored in the harnesses.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

TIMEOUT_SECONDS = 5
SQL_TIMEOUT_SECONDS = 3
TEST_BACKSTOP_SECONDS = 20  # whole-run limit; each Python test also has its own 2s limit in the harness

# --------------------------------------------------------------------------- python

_PY_HARNESS = r'''
import io, json, sys, os, time, threading, traceback, contextlib

PER_TEST_SECONDS = float(os.environ.get("DG_TEST_TIMEOUT", "2"))
sys.setrecursionlimit(4000)
threading.stack_size(128 * 1024 * 1024)

def norm(x):
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted((norm(v) for v in x), key=repr)
    if isinstance(x, float):
        return round(x, 6)
    return x

def short(x, limit=300):
    try:
        s = json.dumps(x, default=repr)
    except Exception:
        s = repr(x)
    return s if len(s) <= limit else s[:limit] + "..."

def user_trace(exc, src_lines):
    # Traceback limited to the learner's own frames, with the offending source line.
    if isinstance(exc, SyntaxError):
        out = ["SyntaxError: %s (line %s)" % (exc.msg, exc.lineno)]
        if exc.text:
            out.append("    " + exc.text.rstrip())
            if exc.offset:
                out.append("    " + " " * max(exc.offset - 1, 0) + "^")
        return "\n".join(out)
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "solution.py"]
    out = []
    for f in frames[-4:]:
        line = src_lines[f.lineno - 1].strip() if 0 < f.lineno <= len(src_lines) else ""
        out.append("line %d, in %s: %s" % (f.lineno, f.name, line))
    head = "%s: %s" % (type(exc).__name__, str(exc)[:300])
    if isinstance(exc, RecursionError):
        head = "RecursionError: maximum recursion depth exceeded (missing base case?)"
    return (head + ("\n  at " + "\n  at ".join(out) if out else ""))[:1500]

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

spec = json.loads(open("spec.json", encoding="utf-8").read())
ns = {"__name__": "solution"}
buf = io.StringIO()
code = open("solution.py", encoding="utf-8").read()
src_lines = code.splitlines()
try:
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "solution.py", "exec"), ns)
except BaseException as exc:
    emit({"compile_error": user_trace(exc, src_lines), "results": []}); sys.exit(0)

fn = ns.get(spec["function"])
if not callable(fn):
    emit({"compile_error": "Function '%s' is not defined. Define it with: def %s(...):" % (spec["function"], spec["function"]), "results": []})
    sys.exit(0)

def call(case, box):
    args, kwargs = case.get("args", []), case.get("kwargs", {}) or {}
    t0 = time.perf_counter()
    try:
        box["got"] = fn(*args, **kwargs)
    except BaseException as exc:
        box["exc"] = exc
    box["ms"] = round((time.perf_counter() - t0) * 1000, 1)

results, hung = [], False
for i, case in enumerate(spec["cases"]):
    args, kwargs = case.get("args", []), case.get("kwargs", {}) or {}
    r = {"index": i, "input": short({"args": args, "kwargs": kwargs} if kwargs else args)}
    expect_exc = case.get("raises")
    if hung:
        r.update(passed=False, expected="(skipped)", got="(skipped after a timeout)")
        results.append(r); continue
    tbuf = io.StringIO(); box = {}
    th = threading.Thread(target=call, args=(case, box), daemon=True)
    with contextlib.redirect_stdout(tbuf):
        th.start(); th.join(PER_TEST_SECONDS)
    if th.is_alive():
        hung = True
        r.update(passed=False, expected=short(case.get("expected")), got="timed out after %gs" % PER_TEST_SECONDS,
                 error="Test %d did not finish in %gs - infinite loop or too slow?" % (i + 1, PER_TEST_SECONDS))
    elif "exc" in box:
        exc = box["exc"]
        names = [c.__name__ for c in type(exc).__mro__]
        if expect_exc and expect_exc in names:
            r.update(passed=True, expected="raises " + expect_exc, got="raised " + type(exc).__name__)
        else:
            r.update(passed=False, expected=("raises " + expect_exc) if expect_exc else short(case.get("expected")),
                     got="%s: %s" % (type(exc).__name__, str(exc)[:200]), error=user_trace(exc, src_lines))
    elif expect_exc:
        r.update(passed=False, expected="raises " + expect_exc, got=short(box.get("got")))
    else:
        got = box.get("got")
        a, b = norm(got), norm(case.get("expected"))
        if case.get("unordered") and isinstance(a, list) and isinstance(b, list):
            a, b = sorted(a, key=repr), sorted(b, key=repr)
        r.update(passed=(a == b), expected=short(case.get("expected")), got=short(got))
        if a != b:
            if got is None and case.get("expected") is not None:
                r["message"] = "function returned None - did you forget `return`?"
            elif isinstance(got, (list, tuple)) and isinstance(case.get("expected"), list) and len(got) != len(case["expected"]):
                r["message"] = "length differs: got %d items, expected %d" % (len(got), len(case["expected"]))
    if "ms" in box:
        r["ms"] = box["ms"]
    if tbuf.getvalue():
        r["stdout"] = tbuf.getvalue()[-500:]
    results.append(r)
emit({"compile_error": None, "results": results, "stdout": buf.getvalue()[-2000:]})
sys.stdout.flush()
os._exit(0)  # don't wait on a hung daemon thread
'''

# --------------------------------------------------------------------------- javascript

_JS_HARNESS = r'''
const fs = require("fs");
const vm = require("vm");
const spec = JSON.parse(fs.readFileSync("spec.json", "utf8"));
const out = { compile_error: null, results: [] };
const logs = [];
const sandbox = { console: { log: (...a) => logs.push(a.map(String).join(" ")) } };
vm.createContext(sandbox);
function norm(x) {
  if (Array.isArray(x)) return x.map(norm);
  if (x && typeof x === "object") {
    const o = {};
    for (const k of Object.keys(x).sort()) o[k] = norm(x[k]);
    return o;
  }
  if (typeof x === "number") return Math.round(x * 1e6) / 1e6;
  return x === undefined ? null : x;
}
const short = (x) => { let s; try { s = JSON.stringify(x); } catch { s = String(x); } s = s === undefined ? "undefined" : s; return s.length > 300 ? s.slice(0, 300) + "..." : s; };
let fn;
try {
  vm.runInContext(fs.readFileSync("solution.js", "utf8") + `\n;globalThis.__fn = typeof ${spec.function} !== "undefined" ? ${spec.function} : undefined;`, sandbox, { timeout: 4000 });
  fn = sandbox.__fn;
  if (typeof fn !== "function") throw new Error(`Function '${spec.function}' is not defined.`);
} catch (e) {
  out.compile_error = String(e && e.stack || e).slice(0, 1500);
  console.log(JSON.stringify(out)); process.exit(0);
}
spec.cases.forEach((c, i) => {
  const args = c.args || [];
  const r = { index: i, input: short(args) };
  try {
    const got = fn(...args);
    if (c.raises) Object.assign(r, { passed: false, expected: "raises " + c.raises, got: short(got) });
    else {
      let a = norm(got), b = norm(c.expected);
      if (c.unordered && Array.isArray(a) && Array.isArray(b)) {
        const key = (v) => JSON.stringify(v);
        a = [...a].sort((p, q) => key(p).localeCompare(key(q)));
        b = [...b].sort((p, q) => key(p).localeCompare(key(q)));
      }
      Object.assign(r, { passed: JSON.stringify(a) === JSON.stringify(b), expected: short(c.expected), got: short(got) });
    }
  } catch (e) {
    const name = e && e.name;
    if (c.raises && (name === c.raises || (c.raises === "Error" && e instanceof Error)))
      Object.assign(r, { passed: true, expected: "raises " + c.raises, got: "raised " + name });
    else Object.assign(r, { passed: false, expected: c.raises ? "raises " + c.raises : short(c.expected), got: `${name}: ${e && e.message}`.slice(0, 300) });
  }
  out.results.push(r);
});
out.stdout = logs.join("\n").slice(-2000);
console.log(JSON.stringify(out));
'''


def _run_process(cmd: list[str], files: dict[str, str], timeout: int = TIMEOUT_SECONDS) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="devgrowth-run-") as tmp:
        for name, content in files.items():
            Path(tmp, name).write_text(content, encoding="utf-8")
        try:
            proc = subprocess.run(
                cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return {"error": f"Timed out after {timeout}s — check for infinite loops or very slow code."}
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if proc.returncode != 0 or not lines:
            return {"error": (proc.stderr or proc.stdout or "Runner crashed")[-1500:]}
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            return {"error": "Could not read runner output:\n" + proc.stdout[-1500:]}


def run_python(code: str, tests: dict) -> dict[str, Any]:
    spec = {"function": tests["function"], "cases": tests.get("cases", [])}
    return _run_process(
        [sys.executable, "-I", "harness.py"],
        {"harness.py": _PY_HARNESS, "solution.py": code, "spec.json": json.dumps(spec)},
        timeout=TEST_BACKSTOP_SECONDS,
    )


def format_syntax_error(exc: SyntaxError) -> str:
    out = [f"SyntaxError: {exc.msg} (line {exc.lineno})"]
    if exc.text:
        out.append("    " + exc.text.rstrip())
        if exc.offset:
            out.append("    " + " " * max(exc.offset - 1, 0) + "^")
    return "\n".join(out)


def check_python_syntax(code: str) -> str | None:
    """Return a readable syntax error, or None if the code compiles."""
    try:
        compile(code, "<code>", "exec")
    except SyntaxError as exc:
        return format_syntax_error(exc)
    return None


def run_python_script(code: str, stdin: str = "") -> dict[str, Any]:
    """Free-form scratch run: execute code as a script (optionally feeding stdin).

    Returns stdout/stderr, wall time, and a tidy error (user-code frames only)."""
    limit = 5000
    err = check_python_syntax(code)
    if err:
        return {"stdout": "", "stderr": err, "timed_out": False, "ms": 0, "limit": TIMEOUT_SECONDS}
    with tempfile.TemporaryDirectory(prefix="devgrowth-scratch-") as tmp:
        Path(tmp, "scratch.py").write_text(code, encoding="utf-8")
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "scratch.py"], cwd=tmp, capture_output=True, text=True,
                input=stdin, timeout=TIMEOUT_SECONDS, encoding="utf-8", errors="replace",
                env={"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                     "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return {"stdout": out[-limit:], "stderr": "", "timed_out": True,
                    "ms": int((time.perf_counter() - t0) * 1000), "limit": TIMEOUT_SECONDS}
    ms = int((time.perf_counter() - t0) * 1000)
    stderr = _tidy_traceback(proc.stderr) if proc.returncode else proc.stderr
    return {"stdout": proc.stdout[-limit:], "stderr": stderr[-limit:], "timed_out": False, "ms": ms,
            "limit": TIMEOUT_SECONDS, "returncode": proc.returncode}


def _tidy_traceback(stderr: str) -> str:
    """Drop the temp-dir path noise: 'File ".../scratch.py", line 3' -> 'line 3'."""
    import re
    return re.sub(r'File "[^"]*scratch\.py", line (\d+)', r'line \1', stderr)


def node_available() -> bool:
    return shutil.which("node") is not None


def run_javascript(code: str, tests: dict) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        return {"error": "Node.js is not installed or not on PATH, so JavaScript can't be auto-checked. Use self-assessment instead."}
    spec = {"function": tests["function"], "cases": tests.get("cases", [])}
    return _run_process(
        [node, "harness.js"],
        {"harness.js": _JS_HARNESS, "solution.js": code, "spec.json": json.dumps(spec)},
    )


# --------------------------------------------------------------------------- sql


def _norm_value(v: Any) -> Any:
    if isinstance(v, float):
        v = round(v, 2)
        return int(v) if v.is_integer() else v
    return v


def _norm_rows(rows: list) -> list[list]:
    return [[_norm_value(v) for v in row] for row in rows]


def run_sql(code: str, tests: dict) -> dict[str, Any]:
    conn = sqlite3.connect(":memory:")
    deadline = time.monotonic() + SQL_TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        try:
            conn.executescript(tests.get("schema_sql", ""))
        except sqlite3.Error as exc:
            return {"error": f"Exercise schema failed to load: {exc}"}
        user_sql = code.strip().rstrip(";").strip()
        if not user_sql:
            return {"error": "Write a query first."}
        try:
            if tests.get("check_sql"):
                conn.executescript(code)
                cur = conn.execute(tests["check_sql"])
            else:
                cur = conn.execute(user_sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            msg = "Query took too long (interrupted)." if "interrupt" in str(exc) else f"SQL error: {exc}"
            return {"compile_error": msg, "results": []}
        except (sqlite3.Error, sqlite3.Warning) as exc:
            msg = str(exc)
            if "one statement at a time" in msg:
                msg = "Submit a single SELECT/WITH statement (remove extra statements)."
            return {"compile_error": f"SQL error: {msg}", "results": []}
    finally:
        conn.close()

    expected = tests.get("expected", {})
    exp_cols = expected.get("columns", [])
    exp_rows = _norm_rows(expected.get("rows", []))
    got_rows = _norm_rows([list(r) for r in rows])
    ordered = bool(tests.get("ordered"))
    col_ok = len(columns) == len(exp_cols)
    if ordered:
        rows_ok = got_rows == exp_rows
    else:
        rows_ok = sorted(got_rows, key=repr) == sorted(exp_rows, key=repr)
    preview_limit = 25
    result = {
        "index": 0,
        "input": "Run against the exercise dataset" + (" (row order matters)" if ordered else " (any row order)"),
        "passed": col_ok and rows_ok,
        "expected": json.dumps({"columns": exp_cols, "rows": exp_rows[:preview_limit]}),
        "got": json.dumps({"columns": columns, "rows": got_rows[:preview_limit]}, default=str),
    }
    if not col_ok:
        result["message"] = f"Expected {len(exp_cols)} columns ({', '.join(exp_cols)}), got {len(columns)}."
    elif not rows_ok:
        result["message"] = f"Expected {len(exp_rows)} rows, got {len(got_rows)}" + (
            " — values or order differ." if len(exp_rows) == len(got_rows) else "."
        )
    return {"compile_error": None, "results": [result], "columns": columns, "rows": got_rows[:50]}


# --------------------------------------------------------------------------- dispatch


def run(language: str, code: str, tests: dict | None) -> dict[str, Any]:
    """Run code; returns {passed, tests_passed, tests_total, results, error?, stdout?}."""
    if not tests:
        return {"error": "This exercise has no automatic tests — use self-assessment.", "passed": False,
                "tests_passed": 0, "tests_total": 0, "results": []}
    if language == "python":
        raw = run_python(code, tests)
    elif language == "javascript":
        raw = run_javascript(code, tests)
    elif language == "sql":
        raw = run_sql(code, tests)
    else:
        raw = {"error": f"No runner for language '{language}'."}

    if raw.get("error"):
        return {"error": raw["error"], "passed": False, "tests_passed": 0,
                "tests_total": _count_cases(language, tests), "results": []}
    results = raw.get("results", [])
    total = _count_cases(language, tests)
    passed_n = sum(1 for r in results if r.get("passed"))
    error = raw.get("compile_error")
    hung = next((r for r in results if "did not finish" in (r.get("error") or "")), None)
    if hung and not error:
        error = "Timed out - " + hung["error"]
    return {
        "error": error,
        "passed": not error and passed_n == total and total > 0,
        "tests_passed": passed_n,
        "tests_total": total,
        "results": results,
        "stdout": raw.get("stdout", ""),
        "columns": raw.get("columns"),
        "rows": raw.get("rows"),
    }


def _count_cases(language: str, tests: dict) -> int:
    return 1 if language == "sql" else len(tests.get("cases", []))
