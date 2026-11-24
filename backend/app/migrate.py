"""Lightweight, additive schema migrations (no Alembic needed for a single-user SQLite app).

`Base.metadata.create_all` creates missing tables but never alters existing ones. This module
adds missing COLUMNS to existing tables (nullable, or NOT NULL with a safe default), after taking
a safety backup. Removing/renaming/retyping columns is deliberately not automated: write a
versioned data migration in MIGRATIONS instead (and bump SCHEMA_VERSION).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import Boolean, Engine, Float, Integer, inspect, text
from sqlalchemy.orm import Session

from .db import Base

log = logging.getLogger("devgrowth.migrate")

SCHEMA_VERSION = 2  # 1 = v2 launch schema; 2 = review_items.ease

# (target_version, function(db)) — one-off data fixes that run once, in order.
MIGRATIONS: list[tuple[int, Callable[[Session], None]]] = []


def _default_literal(col) -> str:
    d = col.default
    if d is not None and getattr(d, "is_scalar", False):
        v = d.arg
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    if isinstance(col.type, Boolean):
        return "0"
    if isinstance(col.type, (Integer, Float)):
        return "0"
    return "''"


def missing_columns(engine: Engine) -> list[tuple[str, str, str]]:
    """[(table, column, ALTER statement)] for model columns absent from the live database."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    out = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all handles whole new tables
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have or col.primary_key:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
            if not col.nullable:
                ddl += f" NOT NULL DEFAULT {_default_literal(col)}"
            out.append((table.name, col.name, ddl))
    return out


def apply_additive(engine: Engine, before_alter: Callable[[], None] | None = None) -> list[str]:
    """Add missing columns. Calls before_alter() (e.g. a backup) once, only if changes are needed."""
    todo = missing_columns(engine)
    if not todo:
        return []
    if before_alter:
        before_alter()
    with engine.begin() as conn:
        for _table, _col, ddl in todo:
            conn.execute(text(ddl))
    done = [f"{t}.{c}" for t, c, _ in todo]
    log.warning("Schema migration added columns: %s", ", ".join(done))
    return done


def run_data_migrations(db: Session, current: int) -> int:
    """Run pending MIGRATIONS above `current`; return the resulting schema version."""
    for version, fn in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version > current:
            fn(db)
            current = version
    return max(current, SCHEMA_VERSION)
