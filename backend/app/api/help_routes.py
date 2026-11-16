"""Handbuch (für alle angemeldeten Benutzer) und Entwickler-API-Referenz (nur Geschäftsführung)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import CurrentUser
from app.services import help as svc

router = APIRouter(tags=["handbuch"])


@router.get("/api/help")
async def toc(_u: CurrentUser) -> Any:
    return {"chapters": svc.toc()}


@router.get("/api/help/search")
async def search(_u: CurrentUser, q: str = "") -> Any:
    return {"results": svc.search(q[:200]) if q.strip() else []}


@router.get("/api/help/chapters/{slug}")
async def chapter(slug: str, _u: CurrentUser) -> Any:
    ch = svc.chapter(slug)
    if ch is None:
        raise HTTPException(status_code=404, detail="Kapitel nicht gefunden")
    return {"slug": ch.slug, "title": ch.title, "summary": ch.summary, "markdown": ch.markdown}


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=500)


@router.post("/api/help/ask")
async def ask(request: Request, data: AskIn, _u: CurrentUser) -> Any:
    provider = getattr(request.app.state, "ai", None)
    try:
        return await svc.ask(provider, data.question)
    except Exception as exc:  # KI nicht erreichbar → Suchtreffer genügen
        return {"answer": None, "sources": [], "hits": svc.search(data.question, 5), "error": f"KI nicht verfügbar ({type(exc).__name__})"}


@router.get("/api/admin/openapi.json")
async def openapi(request: Request, _u: CurrentUser) -> Any:
    """Vollständige OpenAPI-Spezifikation – nur für die Geschäftsführung (Pfad /api/admin/)."""
    spec: dict[str, Any] = request.app.openapi()
    return spec
