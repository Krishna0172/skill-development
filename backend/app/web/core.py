"""Template environment, filters and helpers shared by all page modules."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import markdown as md_lib
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pygments.formatters import HtmlFormatter

from ..enums import EXERCISE_CATEGORY_LABELS, PROFICIENCY_LABELS, ROLES
from . import charts

APP_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

_MD_EXT = ["fenced_code", "codehilite", "tables", "sane_lists", "nl2br"]
_MD_CFG = {"codehilite": {"css_class": "hl", "guess_lang": False}}


def render_md(text: str | None) -> Markup:
    if not text or not str(text).strip():
        return Markup("")
    html = md_lib.markdown(str(text), extensions=_MD_EXT, extension_configs=_MD_CFG)
    return Markup(f'<div class="md">{html}</div>')


def code_block(code: str, lang: str = "python") -> Markup:
    return render_md(f"```{lang}\n{code}\n```")


def pygments_css() -> str:
    light = HtmlFormatter(style="default").get_style_defs("html.light .hl")
    dark = HtmlFormatter(style="github-dark").get_style_defs("html.dark .hl")
    return light + "\n" + dark + "\n.hl pre{background:transparent!important}"


def _parse(v: Any) -> date | datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, (date, datetime)):
        return v
    s = str(v)
    return datetime.fromisoformat(s) if len(s) > 10 else date.fromisoformat(s)


def fmt_date(v: Any, style: str = "medium") -> str:
    d = _parse(v)
    if d is None:
        return "—"
    if style == "short":
        return f"{d.day} {d.strftime('%b')}"
    if style == "long":
        return d.strftime("%A, ") + f"{d.day} " + d.strftime("%B %Y")
    return f"{d.day} {d.strftime('%b %Y')}"


def fmt_datetime(v: Any) -> str:
    d = _parse(v)
    if d is None:
        return "—"
    if isinstance(d, datetime):
        return f"{d.day} {d.strftime('%b')} {d.strftime('%H:%M')}"
    return fmt_date(d)


def rel_days(v: Any) -> str:
    d = _parse(v)
    if d is None:
        return "—"
    d = d.date() if isinstance(d, datetime) else d
    diff = (d - date.today()).days
    if diff == 0:
        return "today"
    if diff == 1:
        return "tomorrow"
    if diff == -1:
        return "yesterday"
    return f"in {diff}d" if diff > 0 else f"{-diff}d ago"


def fmt_minutes(m: Any) -> str:
    m = int(round(float(m or 0)))
    h, mm = divmod(m, 60)
    return f"{h}h {mm}m" if h and mm else f"{h}h" if h else f"{mm}m"


def fmt_hours(h: Any) -> str:
    h = float(h or 0)
    return f"{round(h * 60)}m" if h < 1 else f"{h:.1f}h" if h < 10 else f"{h:.0f}h"


def label(s: Any) -> str:
    if not s:
        return "—"
    s = str(s).replace("_", " ").replace("-", " ")
    return s[:1].upper() + s[1:]


TONES = {
    # topic status
    "not_started": "", "learning": "accent", "practised": "accent", "learned": "good", "mastered": "good",
    "needs_revision": "warn", "difficult": "bad",
    # classification
    "demonstrated": "good", "resume": "", "possibly_familiar": "accent", "not_demonstrated": "bad",
    "needs_revision_c": "warn", "to_learn": "bad",
    # exercise
    "solved": "good", "attempted": "warn", "pending": "",
    # difficulty
    "easy": "good", "medium": "warn", "hard": "bad", "beginner": "good", "intermediate": "warn", "advanced": "bad",
    # curriculum labels
    "revise": "warn", "deepen": "accent", "new": "bad",
    # claim status
    "supported": "good", "partially-supported": "warn", "needs-preparation": "bad", "ready": "good",
    # interview
    "new_q": "", "practising": "accent", "confident": "good",
}

CLASSIFICATION_LABELS = {
    "demonstrated": "Demonstrated in code", "resume": "Mentioned in resume", "possibly_familiar": "Possibly familiar",
    "not_demonstrated": "Not demonstrated", "needs_revision": "Needs revision", "to_learn": "Needs to be learned",
}


def badge(value: Any, text: str | None = None, kind: str | None = None) -> Markup:
    if value is None or value == "":
        return Markup("")
    key = f"{value}_c" if kind == "classification" and value == "needs_revision" else str(value)
    tone = TONES.get(key, "")
    shown = text or (CLASSIFICATION_LABELS.get(str(value)) if kind == "classification" else label(value))
    return Markup(f'<span class="badge {tone}">{Markup.escape(shown)}</span>')


def progress(pct: Any, tone: str = "", thin: bool = False) -> Markup:
    p = max(0.0, min(100.0, float(pct or 0)))
    return Markup(f'<div class="progress {tone}{" thin" if thin else ""}" title="{p:.0f}%"><span style="width:{p:.1f}%"></span></div>')


def dots(value: Any, target: Any = None) -> Markup:
    v = int(value) if value else 0
    t = int(target) if target else 0
    cells = "".join(f'<i class="{"on" if i <= v else "target" if i <= t else ""}"></i>' for i in range(1, 7))
    title = f"Proficiency {v}/6 ({PROFICIENCY_LABELS.get(v, 'not assessed')})" if v else "Not self-assessed yet"
    return Markup(f'<span class="dots" title="{title}">{cells}{"" if v else "<span class=\'tiny faint\'>&nbsp;?</span>"}</span>')


templates.env.filters.update({
    "md": render_md, "date": fmt_date, "datetime": fmt_datetime, "rel": rel_days, "minutes": fmt_minutes,
    "hours": fmt_hours, "label": label, "urlq": lambda s: quote(str(s or "")),
})
templates.env.globals.update({
    "badge": badge, "progress": progress, "dots": dots, "code_block": code_block, "charts": charts,
    "PROFICIENCY_LABELS": PROFICIENCY_LABELS, "ROLES": ROLES, "EXERCISE_CATEGORY_LABELS": EXERCISE_CATEGORY_LABELS,
    "CLASSIFICATION_LABELS": CLASSIFICATION_LABELS, "today": date.today,
})


def _layout_ctx() -> dict[str, Any]:
    """Values the base layout needs on every page (sidebar count, timer, quick-add topics)."""
    from sqlalchemy import func, select

    from ..db import SessionLocal, now
    from ..models import LearningSession, ReviewItem, Skill, Topic

    with SessionLocal() as db:
        due = db.scalar(select(func.count()).select_from(ReviewItem).where(
            ReviewItem.is_active.is_(True), ReviewItem.due_date <= date.today())) or 0
        running = db.scalar(select(LearningSession).where(LearningSession.ended_at.is_(None)))
        timer = None
        if running:
            topic = db.get(Topic, running.topic_id) if running.topic_id else None
            skill = db.get(Skill, running.skill_id) if running.skill_id else None
            timer = {"started_at": running.started_at.isoformat(), "title": running.title, "kind": running.kind,
                     "topic": topic.title if topic else None, "skill": skill.name if skill else None,
                     "minutes": (now() - running.started_at).total_seconds() / 60}
        topics = db.execute(select(Topic.id, Topic.title).where(Topic.track != "none").order_by(Topic.title)).all()
        return {"due_count": due, "timer": timer, "quick_topics": [{"id": i, "title": t} for i, t in topics]}


def render(request: Request, template: str, ctx: dict[str, Any] | None = None, status_code: int = 200) -> HTMLResponse:
    ctx = {**_layout_ctx(), **(ctx or {})}
    ctx.setdefault("theme", request.cookies.get("theme", "dark"))
    ctx.setdefault("path", request.url.path)
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx.setdefault("err", request.query_params.get("err"))
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def redirect(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    url, hash_, fragment = url.partition("#")
    sep = "&" if "?" in url else "?"
    if msg:
        url += f"{sep}msg={quote(msg)}"
        sep = "&"
    if err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url + hash_ + fragment, status_code=303)


def form_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
