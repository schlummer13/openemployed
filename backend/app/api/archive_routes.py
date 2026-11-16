"""Revisionssicheres Dokumentenarchiv: Suche, Ansicht, Upload, Metadaten, Prüfung, Vernichtung nach Fristablauf."""

from __future__ import annotations

import datetime as dt
import os
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import archive as svc
from app.services import archive_sync as sync_svc
from app.services.invoices import today_berlin

router = APIRouter(prefix="/api/archive", tags=["archive"])


@router.get("")
async def search(request: Request, _u: CurrentUser, q: str | None = None, category: str | None = None, year: int | None = None,
                 offset: int = 0) -> Any:
    async def fn(conn: Any) -> Any:
        out = await svc.search(conn, tenant(), q=q, category=category, year=year, offset=max(0, offset))
        out["retention_due"] = await svc.retention_due(conn, tenant(), today_berlin())
        return out
    return money_json(await run(request, fn))


@router.post("/sync")
async def sync(request: Request, user: CurrentUser) -> Any:
    return await run(request, lambda conn: svc.sync(conn, tenant(), actor=user.email))


@router.get("/verify")
async def verify(request: Request, _u: CurrentUser) -> Any:
    return await run(request, lambda conn: svc.verify(conn, tenant()))


# ───────── Spiegelung auf externe Ziele und Vollexport ─────────

class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["s3", "webdav", "folder"]
    config: dict[str, str] = Field(default_factory=dict)
    secret: dict[str, str] = Field(default_factory=dict)
    object_lock: bool = False
    lock_mode: Literal["COMPLIANCE", "GOVERNANCE"] = "COMPLIANCE"
    schedule: Literal["daily", "weekly", "manual"] = "daily"
    enabled: bool = True


@router.get("/targets")
async def targets(request: Request, _u: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: sync_svc.targets(conn, tenant())))


@router.post("/targets", status_code=201)
async def add_target(request: Request, data: TargetIn, user: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: sync_svc.save_target(conn, tenant(), None, data.model_dump(), user.email)))}


@router.put("/targets/{target_id}", status_code=204)
async def put_target(request: Request, target_id: UUID, data: TargetIn, user: CurrentUser) -> None:
    await run(request, lambda conn: sync_svc.save_target(conn, tenant(), target_id, data.model_dump(), user.email))


@router.delete("/targets/{target_id}", status_code=204)
async def del_target(request: Request, target_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: sync_svc.delete_target(conn, tenant(), target_id, user.email))


@router.post("/targets/{target_id}/test")
async def test_target(request: Request, target_id: UUID, _u: CurrentUser) -> Any:
    return await run(request, lambda conn: sync_svc.test_target(conn, tenant(), target_id))


@router.post("/targets/{target_id}/sync")
async def sync_target(request: Request, target_id: UUID, _u: CurrentUser) -> Any:
    try:
        return await run(request, lambda conn: sync_svc.sync_target(conn, tenant(), target_id))
    except sync_svc.TargetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/targets/{target_id}/verify")
async def verify_target(request: Request, target_id: UUID, _u: CurrentUser) -> Any:
    return await run(request, lambda conn: sync_svc.verify_target(conn, tenant(), target_id))


@router.get("/export")
async def export(request: Request, user: CurrentUser, year: int | None = None) -> FileResponse:
    path, name = await run(request, lambda conn: sync_svc.export_zip(conn, tenant(), year=year, actor=user.email))
    return FileResponse(path, media_type="application/zip", filename=name, background=BackgroundTask(os.remove, path))


@router.get("/manifest.csv")
async def manifest(request: Request, _u: CurrentUser) -> Response:
    body = await run(request, lambda conn: sync_svc.manifest_csv(conn, tenant()))
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="archiv-manifest.csv"'})


@router.post("", status_code=201)
async def upload(request: Request, user: CurrentUser, file: Annotated[UploadFile, File()], category: Annotated[str, Form()],
                 title: Annotated[str, Form(min_length=1, max_length=300)], document_date: Annotated[dt.date, Form()],
                 party: Annotated[str | None, Form(max_length=200)] = None,
                 notes: Annotated[str | None, Form(max_length=2000)] = None) -> Any:
    content = await file.read(svc.MAX_BYTES + 1)
    if len(content) > svc.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Datei zu groß (max. 25 MB)")
    mime = (file.content_type or "application/octet-stream").split(";")[0]
    if mime == "application/octet-stream" and (file.filename or "").lower().endswith(".eml"):
        mime = "message/rfc822"
    if document_date > today_berlin():
        raise HTTPException(status_code=422, detail="Dokumentdatum liegt in der Zukunft")
    doc_id = await run(request, lambda conn: svc.store(conn, tenant(), category=category, title=title, filename=file.filename or "datei",
                                                       mime_type=mime, content=content, document_date=document_date, actor=user.email,
                                                       party=party, notes=notes))
    return {"id": str(doc_id)}


@router.get("/{doc_id}")
async def detail(request: Request, doc_id: UUID, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: svc.get(conn, tenant(), doc_id)))


@router.get("/{doc_id}/file")
async def download(request: Request, doc_id: UUID, _u: CurrentUser, inline: bool = True) -> Response:
    doc = await run(request, lambda conn: svc.get(conn, tenant(), doc_id, with_content=True))
    if doc["content"] is None:
        raise HTTPException(status_code=410, detail="Inhalt nach Ablauf der Aufbewahrungsfrist vernichtet")
    disp = "inline" if inline and doc["mime_type"] in ("application/pdf", "image/png", "image/jpeg") else "attachment"
    return Response(bytes(doc["content"]), media_type=doc["mime_type"], headers={
        "Content-Disposition": f'{disp}; filename="{doc["number"]}_{doc["filename"]}"', "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"})


class MetaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


@router.put("/{doc_id}", status_code=204)
async def update(request: Request, doc_id: UUID, data: MetaIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.update_meta(conn, tenant(), doc_id, data.model_dump(), user.email))


@router.post("/{doc_id}/purge", status_code=204)
async def purge(request: Request, doc_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.purge(conn, tenant(), doc_id, user.email, today_berlin()))
