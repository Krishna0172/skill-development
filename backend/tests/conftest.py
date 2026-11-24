"""Shared fixtures: every test gets a fresh app + SQLite database in a temp folder."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SEED_DIR = Path(__file__).resolve().parent.parent / "app" / "seed" / "content"


def _settings(tmp_path: Path, seed: bool) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        backup_dir=tmp_path / "backups",
        seed_dir=SEED_DIR,
        seed_on_startup=seed,
    )


@pytest.fixture
def client(tmp_path):
    """App with the real seed content loaded."""
    with TestClient(create_app(_settings(tmp_path, seed=True))) as c:
        yield c


@pytest.fixture
def empty_client(tmp_path):
    """App with an empty database (no seed content)."""
    with TestClient(create_app(_settings(tmp_path, seed=False))) as c:
        yield c


@pytest.fixture
def settings_for(tmp_path):
    return lambda seed=False: _settings(tmp_path, seed)
