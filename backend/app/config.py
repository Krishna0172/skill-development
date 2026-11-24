"""Application settings, resolved from environment variables with local defaults.

All paths default to folders inside the project so the app is self-contained on Windows.
Override with environment variables (useful for tests):

    SKILLDEV_DATA_DIR      folder holding the SQLite DB and backups (default: backend/data)
    SKILLDEV_DATABASE_URL  full SQLAlchemy URL (default: sqlite file in the data dir)
    SKILLDEV_SEED          "0" disables seeding reference content on startup
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    backup_dir: Path
    seed_dir: Path
    seed_on_startup: bool
    max_auto_backups: int = 14

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        return Path(self.database_url.removeprefix("sqlite:///"))


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("SKILLDEV_DATA_DIR", BACKEND_DIR / "data"))
    db_url = os.environ.get(
        "SKILLDEV_DATABASE_URL", f"sqlite:///{(data_dir / 'skilldev.db').as_posix()}"
    )
    return Settings(
        data_dir=data_dir,
        database_url=db_url,
        backup_dir=data_dir / "backups",
        seed_dir=Path(__file__).resolve().parent / "seed" / "content",
        seed_on_startup=os.environ.get("SKILLDEV_SEED", "1") != "0",
    )
