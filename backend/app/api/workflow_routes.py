"""Eigene Workflows: anlegen, bearbeiten, testen, Protokoll."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.domain.workflows import OPS
from app.services import events
from app.services import workflows as svc

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("")
async def overview(request: Request, _u: CurrentUser) -> Any:
    from sqlalchemy import text

    async def fn(conn: Any) -> Any:
        projects = [dict(r) for r in (await conn.execute(text(
            "SELECT id, name FROM projects WHERE tenant_id = :t AND archived_at IS NULL ORDER BY name"), {"t": tenant()})).mappings()]
        return {"workflows": await svc.list_workflows(conn, tenant()), "runs": await svc.runs(conn, tenant(), limit=30),
                "events": events.PUBLIC, "step_types": svc.STEP_TYPES, "ops": OPS, "projects": projects}
    return money_json(await run(request, fn))


@router.get("/fields")
async def fields(request: Request, _u: CurrentUser, event: str | None = None) -> Any:
    return {"paths": await run(request, lambda conn: svc.field_paths(conn, tenant(), event))}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_]+$")
    type: Literal["if", "condition", "email", "task", "http", "python"]
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = False
    trigger_type: Literal["cron", "event", "manual"]
    trigger_value: str | None = Field(default=None, max_length=100)
    steps: list[Step] = Field(default_factory=list, max_length=40)


@router.post("", status_code=201)
async def create(request: Request, data: WorkflowIn, user: CurrentUser) -> Any:
    wid = await run(request, lambda conn: svc.save(conn, tenant(), data.model_dump(), user.email))
    return {"id": str(wid)}


@router.put("/{wid}", status_code=204)
async def update(request: Request, wid: UUID, data: WorkflowIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.save(conn, tenant(), data.model_dump(), user.email, wid))


@router.delete("/{wid}", status_code=204)
async def delete(request: Request, wid: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.delete(conn, tenant(), wid, user.email))


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any] = Field(default_factory=dict)


@router.post("/{wid}/run")
async def run_now(request: Request, wid: UUID, body: RunIn, user: CurrentUser) -> Any:
    """Sofort ausführen (Test oder manueller Auslöser) – mit Beispieldaten für die Platzhalter."""
    rid = await run(request, lambda conn: _enqueue(conn, wid, body.data, user.email))
    ctx = SimpleNamespace(engine=request.app.state.engine, mailer=getattr(request.app.state, "mailer", None), tenant_id=tenant())
    status = await svc.execute(ctx, rid)
    runs = await run(request, lambda conn: svc.runs(conn, tenant(), wid, limit=1))
    return money_json({"status": status, "run": runs[0] if runs else None})


async def _enqueue(conn: Any, wid: UUID, data: dict[str, Any], actor: str) -> UUID:
    await svc.get(conn, tenant(), wid)
    return await svc.enqueue(conn, tenant(), wid, f"manuell:{actor}", {"data": data})
