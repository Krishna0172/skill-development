"""Backup, restore, export and import."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..db import SessionLocal, get_db
from ..models import AppMeta
from ..services import backup

router = APIRouter(tags=["data"])

MAX_IMPORT_BYTES = 200 * 1024 * 1024


@router.get("/meta")
def meta(request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    rows = {m.key: m.value for m in db.scalars(select(AppMeta))}
    path = settings.sqlite_path
    return {"version": __version__, **rows, "database": str(path) if path else settings.database_url.split("@")[-1],
            "database_size_bytes": path.stat().st_size if path and path.exists() else None,
            "backup_dir": str(settings.backup_dir)}


@router.get("/data/export")
def export_json(db: Session = Depends(get_db)):
    payload = backup.export_all(db)
    name = f"devgrowth-export-{datetime.now():%Y%m%d-%H%M%S}.json"
    return Response(json.dumps(payload, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/data/import")
async def import_json(request: Request, file: UploadFile = File(...)):
    """Replace all data with an export file. A safety backup is taken first."""
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "File too large")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(422, "File is not valid JSON") from exc
    settings = request.app.state.settings
    safety = None
    if settings.sqlite_path:
        safety = backup.create_backup(settings, "pre-import").name
    with SessionLocal() as db:
        try:
            counts = backup.import_all(db, payload)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            db.rollback()
            raise HTTPException(422, f"Import failed, nothing was changed: {exc}") from exc
    return {"imported": counts, "safety_backup": safety}


@router.get("/backups")
def list_backups(request: Request):
    return backup.list_backups(request.app.state.settings)


@router.post("/backups", status_code=201)
def create_backup(request: Request):
    try:
        path = backup.create_backup(request.app.state.settings, "manual")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": path.name}


@router.get("/backups/{name}")
def download_backup(name: str, request: Request):
    try:
        path = backup.backup_path(request.app.state.settings, name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Backup not found") from exc
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.post("/backups/{name}/restore")
def restore(name: str, request: Request):
    try:
        safety = backup.restore_backup(request.app.state.settings, name)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Backup not found") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"restored": name, "safety_backup": safety.name}


@router.delete("/backups/{name}", status_code=204)
def delete_backup(name: str, request: Request):
    try:
        backup.backup_path(request.app.state.settings, name).unlink()
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Backup not found") from exc
