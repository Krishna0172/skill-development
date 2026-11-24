from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import search as search_service

router = APIRouter(tags=["search"])


@router.get("/search")
def search(q: str, types: str | None = None, category: str | None = None, skill_id: int | None = None,
           limit: int = 40, db: Session = Depends(get_db)):
    wanted = [t for t in (types or "").split(",") if t in search_service.TYPES] or None
    results = search_service.search(db, q, wanted, category, skill_id, max(1, min(limit, 200)))
    return {"query": q, "count": len(results), "results": results}
