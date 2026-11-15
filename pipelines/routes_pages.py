"""
Readable pages (router; pipelines/app.py includes it with one line).

    GET /api/page/{name}  ->  {id, title, developer, label, blocks}   name: 'tutorial' | 'adc-answer' (usability.PAGES)

The two documents the Generalization tab links to, as text blocks the page shows itself (the page escapes every block;
no HTML is passed through). Only the names in usability.PAGES are served, so no other file can be read through this route.
"""
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usability  # noqa: E402

router = APIRouter()


@router.get("/api/page/{name}")
def api_page(name: str):
    """One readable page; 404 for a name that is not listed."""
    page = usability.readable_page(name, usability.ROOT)
    if page is None:
        raise HTTPException(404, "No such page.")
    return page
