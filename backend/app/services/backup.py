"""Backups, restore, and portable JSON export/import.

- File backups use SQLite's online backup API (consistent even while the app is running).
- A backup is taken automatically once per day on startup and before every restore/import.
- JSON export contains every table, so data can be moved to PostgreSQL or inspected by hand.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import Settings
from ..db import Base, get_engine, init_engine

BACKUP_RE = re.compile(r"^skilldev-(\d{8}-\d{6})(?:-([a-z0-9-]+))?\.db$")
EXPORT_FORMAT = "devgrowth-export"
EXPORT_VERSION = 1


def _require_sqlite(settings: Settings) -> Path:
    path = settings.sqlite_path
    if path is None:
        raise RuntimeError("File backups are only available for the SQLite database. Use JSON export instead.")
    return path


def create_backup(settings: Settings, label: str = "manual") -> Path:
    db_path = _require_sqlite(settings)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    label = re.sub(r"[^a-z0-9-]", "", label.lower())[:30] or "manual"
    target = settings.backup_dir / f"skilldev-{datetime.now():%Y%m%d-%H%M%S}-{label}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def list_backups(settings: Settings) -> list[dict[str, Any]]:
    if not settings.backup_dir.is_dir():
        return []
    out = []
    for path in settings.backup_dir.glob("skilldev-*.db"):
        m = BACKUP_RE.match(path.name)
        if not m:
            continue
        out.append({
            "name": path.name,
            "created_at": datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").isoformat(),
            "label": m.group(2) or "",
            "size_bytes": path.stat().st_size,
        })
    return sorted(out, key=lambda b: b["created_at"], reverse=True)


def backup_path(settings: Settings, name: str) -> Path:
    if not BACKUP_RE.match(name):
        raise ValueError("Invalid backup name")
    path = settings.backup_dir / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def auto_daily_backup(settings: Settings) -> Path | None:
    """Take one 'auto' backup per day and keep the most recent N auto backups."""
    if settings.sqlite_path is None or not settings.sqlite_path.exists():
        return None
    today = f"skilldev-{date.today():%Y%m%d}-"
    autos = sorted(p for p in settings.backup_dir.glob("skilldev-*-auto.db"))
    if any(p.name.startswith(today) for p in autos):
        return None
    created = create_backup(settings, "auto")
    autos = sorted(settings.backup_dir.glob("skilldev-*-auto.db"))
    for old in autos[: max(0, len(autos) - settings.max_auto_backups)]:
        old.unlink(missing_ok=True)
    return created


def restore_backup(settings: Settings, name: str) -> Path:
    """Replace the live database with a backup (after backing up the current state)."""
    source = backup_path(settings, name)
    _validate_sqlite_file(source)
    safety = create_backup(settings, "pre-restore")
    db_path = _require_sqlite(settings)
    get_engine().dispose()
    src = sqlite3.connect(source)
    dst = sqlite3.connect(db_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    init_engine(settings.database_url)
    return safety


def _validate_sqlite_file(path: Path) -> None:
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Not a valid database file: {exc}") from exc
    required = {"skills", "topics", "learning_sessions", "app_meta"}
    if not required <= tables:
        raise ValueError("This file is not a DevGrowth database (missing tables).")


# --------------------------------------------------------------------------- JSON export/import


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def export_all(db: Session) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in Base.metadata.sorted_tables:
        rows = db.execute(select(table)).mappings().all()
        tables[table.name] = [{k: _jsonable(v) for k, v in row.items()} for row in rows]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tables": tables,
    }


def _coerce(column, value: Any) -> Any:
    if value is None:
        return None
    pytype = None
    try:
        pytype = column.type.python_type
    except NotImplementedError:
        return value
    if pytype is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if pytype is date and isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


def import_all(db: Session, payload: dict[str, Any]) -> dict[str, int]:
    """Replace ALL data with the export payload (validated first, single transaction)."""
    if payload.get("format") != EXPORT_FORMAT or not isinstance(payload.get("tables"), dict):
        raise ValueError("Not a DevGrowth export file.")
    if payload.get("version", 0) > EXPORT_VERSION:
        raise ValueError("This export was made by a newer version of the app.")
    known = {t.name: t for t in Base.metadata.sorted_tables}
    unknown = set(payload["tables"]) - set(known)
    if unknown:
        raise ValueError(f"Unknown tables in export: {', '.join(sorted(unknown))}")

    counts: dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(delete(table))
    for table in Base.metadata.sorted_tables:
        rows = payload["tables"].get(table.name, [])
        cols = {c.name: c for c in table.columns}
        clean = [{k: _coerce(cols[k], v) for k, v in row.items() if k in cols} for row in rows]
        if clean:
            db.execute(insert(table), clean)
        counts[table.name] = len(clean)
    db.commit()
    return counts
