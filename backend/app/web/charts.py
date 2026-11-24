"""Server-rendered SVG charts (no JavaScript).

Rules followed: one y-axis per chart, thin marks with rounded data ends, recessive grid,
a native <title> tooltip on every mark (hover), colours from CSS variables (--series-N /
--seq-N) so dark and light themes both work, legend only when there are 2+ series.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from html import escape
from typing import Any, Callable, Sequence

from markupsafe import Markup


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def _fmt(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() or v >= 10 else f"{v:.1f}"


def _empty(height: int, text: str = "No recorded activity in this range yet") -> Markup:
    return Markup(f'<div class="chart-empty" style="height:{height}px">{escape(text)}</div>')


def bars(rows: Sequence[dict[str, Any]], x: str, series: list[tuple[str, str]], *, height: int = 200,
         fmt_x: Callable[[Any], str] = str, fmt_v: Callable[[float], str] = _fmt, stacked: bool = False,
         label_every: int | None = None) -> Markup:
    """Vertical bars over a category/time axis. series = [(key, name)]."""
    if not rows or all(not (r.get(k) or 0) for r in rows for k, _ in series):
        return _empty(height)
    w, h, left, bottom, top = 640, height, 36, 22, 8
    plot_w, plot_h = w - left - 6, h - bottom - top
    totals = [sum(r.get(k) or 0 for k, _ in series) if stacked else max(r.get(k) or 0 for k, _ in series) for r in rows]
    vmax = _nice_max(max(totals))
    n = len(rows)
    slot = plot_w / n
    groups = 1 if stacked else len(series)
    bar_w = max(1.5, min(26, slot * 0.72 / groups))
    every = label_every or max(1, math.ceil(n / 10))
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img">']
    for i in range(5):  # grid + y labels
        val = vmax * i / 4
        y = top + plot_h - plot_h * i / 4
        out.append(f'<line x1="{left}" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" class="axis" text-anchor="end">{escape(fmt_v(val))}</text>')
    for i, r in enumerate(rows):
        cx = left + slot * i + slot / 2
        base = top + plot_h
        for j, (key, name) in enumerate(series):
            v = r.get(key) or 0
            bh = plot_h * v / vmax
            if stacked:
                x0 = cx - bar_w / 2
                y0 = base - bh
                base = y0
            else:
                x0 = cx - bar_w * groups / 2 + j * bar_w
                y0 = top + plot_h - bh
            if v:
                rx = min(3, bar_w / 2)
                out.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{max(bar_w - 1.5, 1):.1f}" height="{bh:.1f}" rx="{rx}" '
                           f'fill="var(--series-{j + 1})"><title>{escape(fmt_x(r[x]))} · {escape(name)}: {escape(fmt_v(v))}</title></rect>')
        if i % every == 0:
            out.append(f'<text x="{cx:.1f}" y="{h - 6}" class="axis" text-anchor="middle">{escape(fmt_x(r[x]))}</text>')
    out.append("</svg>")
    return Markup("".join(out) + _legend(series))


def line(rows: Sequence[dict[str, Any]], x: str, series: list[tuple[str, str]], *, height: int = 200,
         fmt_x: Callable[[Any], str] = str, fmt_v: Callable[[float], str] = _fmt, y_max: float | None = None) -> Markup:
    pts_all = [r.get(k) for r in rows for k, _ in series if r.get(k) is not None]
    if not rows or not pts_all:
        return _empty(height)
    w, h, left, bottom, top = 640, height, 36, 22, 8
    plot_w, plot_h = w - left - 10, h - bottom - top
    vmax = y_max or _nice_max(max(pts_all))
    n = len(rows)
    step = plot_w / max(1, n - 1)
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img">']
    for i in range(5):
        val = vmax * i / 4
        y = top + plot_h - plot_h * i / 4
        out.append(f'<line x1="{left}" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" class="axis" text-anchor="end">{escape(fmt_v(val))}</text>')
    every = max(1, math.ceil(n / 8))
    for i, r in enumerate(rows):
        if i % every == 0:
            out.append(f'<text x="{left + step * i:.1f}" y="{h - 6}" class="axis" text-anchor="middle">{escape(fmt_x(r[x]))}</text>')
    for j, (key, name) in enumerate(series):
        pts = [(left + step * i, top + plot_h - plot_h * (r[key] / vmax), r) for i, r in enumerate(rows) if r.get(key) is not None]
        if len(pts) > 1:
            d = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py, _) in enumerate(pts))
            out.append(f'<path d="{d}" fill="none" stroke="var(--series-{j + 1})" stroke-width="2" vector-effect="non-scaling-stroke"/>')
        for px, py, r in pts:
            out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="var(--series-{j + 1})" stroke="var(--surface)" stroke-width="1.5">'
                       f'<title>{escape(fmt_x(r[x]))} · {escape(name)}: {escape(fmt_v(r[key]))}</title></circle>')
    out.append("</svg>")
    return Markup("".join(out) + _legend(series))


def hbars(rows: Sequence[dict[str, Any]], label: str, value: str, *, name: str = "", vmax: float | None = None,
          fmt_v: Callable[[float], str] = _fmt) -> Markup:
    """Ranked horizontal bars rendered as HTML rows (crisp text, no scaling issues)."""
    if not rows:
        return _empty(80, "Nothing to compare yet")
    top = vmax or max((r[value] or 0) for r in rows) or 1
    out = ['<div class="hbars">']
    for r in rows:
        v = r[value] or 0
        out.append(f'<div class="hbar" title="{escape(str(r[label]))} · {escape(name)}: {escape(fmt_v(v))}">'
                   f'<span class="hbar-label">{escape(str(r[label]))}</span>'
                   f'<span class="hbar-track"><span class="hbar-fill" style="width:{100 * v / top:.1f}%"></span></span>'
                   f'<span class="hbar-val">{escape(fmt_v(v))}</span></div>')
    out.append("</div>")
    return Markup("".join(out))


def segments(parts: list[tuple[str, float, str]]) -> Markup:
    """Part-to-whole strip with legend. parts = [(label, value, css colour)]."""
    total = sum(v for _, v, _ in parts)
    if not total:
        return Markup('<div class="seg-empty"></div>')
    bar = "".join(f'<span style="width:{100 * v / total:.2f}%;background:{c}" title="{escape(l)}: {_fmt(v)}"></span>'
                  for l, v, c in parts if v)
    legend = "".join(f'<span class="legend-item"><i style="background:{c}"></i>{escape(l)} <b>{_fmt(v)}</b></span>'
                     for l, v, c in parts if v)
    return Markup(f'<div class="segbar">{bar}</div><div class="legend">{legend}</div>')


def heatmap(days: Sequence[dict[str, Any]], value: str = "minutes", *, tip: Callable[[dict], str] | None = None,
            href: Callable[[dict], str] | None = None, selected: str | None = None) -> Markup:
    """GitHub-style calendar, Monday-first columns, sequential single-hue ramp."""
    if not days:
        return Markup("")
    first = date.fromisoformat(days[0]["date"])
    lead = first.weekday()
    cells: list[dict | None] = [None] * lead + list(days)
    cols = math.ceil(len(cells) / 7)
    size, gap = 11, 3
    w, h = cols * (size + gap) + 2, 7 * (size + gap) + 2
    out = [f'<svg class="heatmap" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img">']
    for idx, d in enumerate(cells):
        if d is None:
            continue
        col, row = divmod(idx, 7)
        v = d.get(value) or 0
        lv = 0 if v <= 0 else 1 if v < 15 else 2 if v < 45 else 3 if v < 90 else 4
        if d.get("active") and not v:
            lv = 1
        label = tip(d) if tip else f"{d['date']}: {v}"
        ring = ' stroke="var(--accent)" stroke-width="2"' if selected == d["date"] else ""
        rect = (f'<rect x="{col * (size + gap) + 1}" y="{row * (size + gap) + 1}" width="{size}" height="{size}" rx="2.5" '
                f'fill="var(--seq-{lv})"{ring}><title>{escape(label)}</title></rect>')
        out.append(f'<a href="{escape(href(d))}">{rect}</a>' if href else rect)
    out.append("</svg>")
    scale = "".join(f'<i style="background:var(--seq-{i})"></i>' for i in range(5))
    return Markup(f'<div class="heatmap-wrap">{"".join(out)}</div><div class="heat-scale">Less {scale} More</div>')


def ring(percent: float, *, size: int = 64, label: str = "") -> Markup:
    """Small progress ring for a single headline percentage."""
    r = size / 2 - 5
    c = 2 * math.pi * r
    p = max(0.0, min(100.0, percent or 0))
    return Markup(
        f'<svg class="ring" width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img"><title>{escape(label)} {p:.0f}%</title>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none" stroke="var(--surface-3)" stroke-width="6"/>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none" stroke="var(--accent)" stroke-width="6" stroke-linecap="round" '
        f'stroke-dasharray="{c * p / 100:.1f} {c:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/>'
        f'<text x="50%" y="54%" text-anchor="middle" class="ring-text">{p:.0f}%</text></svg>')


def _legend(series: list[tuple[str, str]]) -> str:
    if len(series) < 2:
        return ""
    items = "".join(f'<span class="legend-item"><i style="background:var(--series-{i + 1})"></i>{escape(name)}</span>'
                    for i, (_, name) in enumerate(series))
    return f'<div class="legend">{items}</div>'


def short_date(v: str) -> str:
    d = date.fromisoformat(v[:10])
    return f"{d.day} {d.strftime('%b')}"


def weekday(v: str) -> str:
    return date.fromisoformat(v[:10]).strftime("%a")


def month_label(v: str) -> str:
    return date.fromisoformat(f"{v}-01").strftime("%b %y")


def last_n_days(n: int) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
