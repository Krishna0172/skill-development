"""Analytics (10 charts over a range), weekly/monthly reports, and settings (backups, export/import)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..routers import data as data_api
from ..services import backup, reports, stats
from .core import redirect, render

router = APIRouter(include_in_schema=False)

RANGES = [("7d", "7 days"), ("30d", "30 days"), ("90d", "90 days"), ("180d", "6 months"), ("365d", "1 year"), ("all", "All time")]


@router.get("/analytics")
def analytics_page(request: Request, range: str = "30d", start: str = "", end: str = "", db: Session = Depends(get_db)):
    try:
        s = date.fromisoformat(start) if start else None
        e = date.fromisoformat(end) if end else None
        key = "custom" if (s and e) else (range if range in dict(RANGES) else "30d")
        s, e = stats.resolve_range(db, key, s, e)
        if (e - s).days > 3660:
            raise ValueError("Range too large (max 10 years)")
    except ValueError as exc:
        return redirect("/analytics", err=str(exc))
    a = stats.analytics(db, s, e)
    long_range = (e - s).days > 120
    return render(request, "analytics.html", {"a": a, "range_key": key, "ranges": RANGES, "start": s, "end": e,
                                              "long_range": long_range})


# --------------------------------------------------------------------------- reports


@router.get("/reports")
def reports_page(request: Request, kind: str = "weekly", week: str = "", month: str = "", db: Session = Depends(get_db)):
    today = date.today()
    if kind == "monthly":
        try:
            y, m = (int(x) for x in (month or today.strftime("%Y-%m")).split("-"))
            if not 1 <= m <= 12:
                raise ValueError
        except ValueError:
            return redirect("/reports?kind=monthly", err="Month must be YYYY-MM.")
        r = reports.monthly(db, y, m)
        prev_m = (date(y, m, 1) - timedelta(days=1)).strftime("%Y-%m")
        next_m = (date(y, m, 28) + timedelta(days=4)).replace(day=1)
        nav = {"prev": f"/reports?kind=monthly&month={prev_m}",
               "next": f"/reports?kind=monthly&month={next_m:%Y-%m}" if next_m <= today else None,
               "csv": f"/api/reports/monthly?month={y:04d}-{m:02d}&format=csv", "title": date(y, m, 1).strftime("%B %Y")}
    else:
        kind = "weekly"
        try:
            ws = date.fromisoformat(week) if week else today
        except ValueError:
            ws = today
        r = reports.weekly(db, ws)
        start = date.fromisoformat(r["period"]["start"])
        nav = {"prev": f"/reports?week={start - timedelta(days=7)}",
               "next": f"/reports?week={start + timedelta(days=7)}" if start + timedelta(days=7) <= today else None,
               "csv": f"/api/reports/weekly?week_start={start}&format=csv",
               "title": f"Week of {start:%d %b %Y}"}
    return render(request, "reports.html", {"r": r, "kind": kind, "nav": nav})


# --------------------------------------------------------------------------- settings


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "settings.html", {"meta": data_api.meta(request, db),
                                             "backups": backup.list_backups(request.app.state.settings)})


@router.post("/settings/backup")
def settings_backup(request: Request):
    try:
        out = data_api.create_backup(request)
    except HTTPException as exc:
        return redirect("/settings", err=str(exc.detail))
    return redirect("/settings", f"Backup created: {out['name']}")


@router.post("/settings/backups/{name}/restore")
def settings_restore(name: str, request: Request):
    try:
        out = data_api.restore(name, request)
    except HTTPException as exc:
        return redirect("/settings", err=str(exc.detail))
    return redirect("/settings", f"Restored {out['restored']}. Your previous data was saved as {out['safety_backup']}.")


@router.post("/settings/backups/{name}/delete")
def settings_delete_backup(name: str, request: Request):
    try:
        data_api.delete_backup(name, request)
    except HTTPException as exc:
        return redirect("/settings", err=str(exc.detail))
    return redirect("/settings", "Backup deleted.")


@router.post("/settings/import")
async def settings_import(request: Request, file: UploadFile = File(...)):
    try:
        out = await data_api.import_json(request, file)
    except HTTPException as exc:
        return redirect("/settings", err=str(exc.detail))
    total = sum(out["imported"].values())
    return redirect("/settings", f"Imported {total} records."
                    + (f" Previous data saved as {out['safety_backup']}." if out["safety_backup"] else ""))
