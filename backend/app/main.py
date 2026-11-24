"""FastAPI application factory.

Run (from backend/):  .venv\\Scripts\\python -m uvicorn app.main:app --port 8765
Pages are server-rendered (Jinja2 + HTMX, see app/web); the JSON API lives under /api.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import __version__, migrate
from .config import Settings, load_settings
from .db import Base, SessionLocal, get_engine, init_engine, now
from .models import AppMeta
from .routers import (
    analytics,
    data,
    goals,
    interview,
    knowledge,
    practice,
    projects,
    revision,
    roadmap,
    search,
    skills,
    tracking,
)
from .seed.loader import seed_all
from .services import backup

log = logging.getLogger("devgrowth")

def create_schema(settings: Settings | None = None) -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    # additive migration (new columns on existing tables); backs up first if anything must change
    pre = (lambda: backup.create_backup(settings, "pre-migrate")) if settings and settings.sqlite_path else None
    migrate.apply_additive(engine, pre)
    with SessionLocal() as db:
        meta = {m.key: m for m in db.scalars(select(AppMeta))}
        if "tracking_started_on" not in meta:
            db.add(AppMeta(key="tracking_started_on", value=date.today().isoformat()))
        if "created_at" not in meta:
            db.add(AppMeta(key="created_at", value=now().isoformat()))
        current = int(meta["schema_version"].value) if "schema_version" in meta else 0
        if current < migrate.SCHEMA_VERSION:
            new_version = migrate.run_data_migrations(db, current)
            if "schema_version" in meta:
                meta["schema_version"].value = str(new_version)
            else:
                db.add(AppMeta(key="schema_version", value=str(new_version)))
        db.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        init_engine(settings.database_url)
        create_schema(settings)
        if settings.seed_on_startup:
            with SessionLocal() as db:
                result = seed_all(db, settings.seed_dir)
                if result["inserted"]:
                    log.info("Seeded reference content: %s", result["inserted"])
        try:
            backup.auto_daily_backup(settings)
        except Exception:  # never block startup on a backup problem
            log.exception("Automatic backup failed")
        yield

    app = FastAPI(title="DevGrowth", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(IntegrityError)
    async def integrity_error(_request: Request, exc: IntegrityError):
        msg = str(exc.orig) if exc.orig else str(exc)
        if "UNIQUE" in msg.upper():
            return JSONResponse(status_code=409, content={"detail": "This record already exists (duplicate)."})
        return JSONResponse(status_code=409, content={"detail": f"Database constraint failed: {msg[:200]}"})

    for module in (skills, knowledge, revision, practice, interview, projects, roadmap, goals, tracking,
                   analytics, search, data):
        app.include_router(module.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    # ---- server-rendered web UI (Jinja2 + a little HTMX) ----
    from .web import PAGE_ROUTERS
    from .web.core import pygments_css, render

    static_dir = Path(__file__).resolve().parent / "static"
    css = pygments_css()

    @app.get("/static/pygments.css", include_in_schema=False)  # registered before the mount so it wins
    def _pygments() -> Response:
        return Response(css, media_type="text/css")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    for page_router in PAGE_ROUTERS:
        app.include_router(page_router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=404, content={"detail": getattr(exc, "detail", "Not found")})
        return render(request, "error.html", {"title": "Not found", "detail": getattr(exc, "detail", "")}, status_code=404)

    return app


app = create_app()
