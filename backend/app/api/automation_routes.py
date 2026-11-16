"""API: Automatisierungen, Dashboard (Kennzahlen + Aufgaben)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import automation as au
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api", tags=["automatisierung"])


def context(request: Request, actor: str) -> au.Context:
    st = request.app.state
    return au.Context(engine=st.engine, ledger=st.ledger, mailer=getattr(st, "mailer", None),
                      extractor=getattr(st, "extractor", None), tenant_id=tenant(), actor=actor)


@router.get("/automations")
async def list_automations(request: Request, user: CurrentUser) -> Any:
    ctx = context(request, user.email)
    return jsonable_encoder(await run(request, lambda conn: au.overview(conn, ctx)))


class ToggleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.put("/automations/{key}")
async def toggle(request: Request, key: str, data: ToggleIn, user: CurrentUser) -> Any:
    if key not in au.BY_KEY:
        raise HTTPException(status_code=404, detail="Automatisierung nicht gefunden")
    await run(request, lambda conn: au.set_enabled(conn, tenant(), key, data.enabled, user.email))
    return {"key": key, "enabled": data.enabled}


@router.post("/automations/{key}/run")
async def run_now(request: Request, key: str, user: CurrentUser) -> Any:
    job = au.BY_KEY.get(key)
    if job is None:
        raise HTTPException(status_code=404, detail="Automatisierung nicht gefunden")
    return await au.run_job(context(request, user.email), job, "manual", today_berlin())


@router.get("/dashboard")
async def dashboard(request: Request, _u: CurrentUser) -> Any:
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        from app.core.read_cache import cached
        from app.services import finance, projects

        today = today_berlin()

        async def compute() -> Any:
            holders = (await conn.execute(text("SELECT id, name, color, photo IS NOT NULL AS has_photo FROM shareholders "
                                               "WHERE tenant_id = :t ORDER BY name"), {"t": tenant()})).mappings().all()
            return jsonable_encoder(to_ledger_payload({
                "kpis": await au.kpis(conn, tenant(), today), "tasks": await au.open_tasks(conn, tenant(), today, ledger),
                "capital": await finance.capital(conn, tenant(), today, ledger), "months": await finance.monthly(conn, tenant(), today),
                "forecast": await finance.forecast(conn, tenant(), today, ledger=ledger),
                "project_tasks": await projects.open_tasks(conn, tenant()), "shareholders": [dict(h) for h in holders]}))
        return await cached(conn, ("dashboard", tenant(), today), compute)
    return await run(request, fn)


async def _ai_ready(request: Request) -> bool:
    provider = getattr(request.app.state, "ai", None)
    if provider is None:
        return False
    ready, _ = await provider.status()
    return bool(ready)


@router.get("/setup")
async def setup_status(request: Request, _u: CurrentUser) -> Any:
    """Einrichtungs-Checkliste für den Produktivstart."""
    from app.services import setup

    ai_ready = await _ai_ready(request)
    return money_json(await run(request, lambda conn: setup.status(conn, tenant(), today_berlin(), ai_ready)))


class SetupFlagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool


@router.put("/setup/{key}", status_code=204)
async def set_setup_flag(request: Request, key: str, data: SetupFlagIn, user: CurrentUser) -> Response:
    from app.services import setup

    await run(request, lambda conn: setup.set_flag(conn, tenant(), key, data.done, user.email))
    return Response(status_code=204)


@router.get("/inbox")
async def inbox(request: Request, _u: CurrentUser) -> Any:
    """Zentrale Liste „Zu erledigen“: alle offenen Punkte der Fachbereiche plus offene Projektaufgaben."""
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        from app.core.read_cache import cached
        from app.services import projects

        today = today_berlin()

        async def compute() -> Any:
            return money_json({"tasks": await au.open_tasks(conn, tenant(), today, ledger),
                               "project_tasks": await projects.open_tasks(conn, tenant(), limit=100)})
        return await cached(conn, ("inbox", tenant(), today), compute)
    return await run(request, fn)


@router.get("/receipt-inbox/unassigned")
async def unassigned_files(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        rows = await conn.execute(text(
            "SELECT f.id, f.file_name, f.mime_type, f.uploaded_by, f.uploaded_at FROM receipt_files f WHERE f.tenant_id = :t "
            "AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.file_id = f.id) ORDER BY f.uploaded_at DESC LIMIT 100"), {"t": tenant()})
        return [dict(r) for r in rows.mappings()]
    return jsonable_encoder(await run(request, fn))



class FinanceSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_liquidity: Decimal = Decimal("5000")
    monthly_fixed_costs: Decimal = Decimal("0")
    offer_followup_days: int = 7
    offer_auto_reminder: bool = False
    welcome_mail: bool = True


@router.get("/finance/settings")
async def get_finance_settings(request: Request, _u: CurrentUser) -> Any:
    from app.services import finance

    return jsonable_encoder(to_ledger_payload(await run(request, lambda conn: finance.settings(conn, tenant()))))


@router.put("/finance/settings")
async def put_finance_settings(request: Request, data: FinanceSettingsIn, user: CurrentUser) -> Any:
    from app.services import finance

    if data.min_liquidity < 0 or data.monthly_fixed_costs < 0 or not 2 <= data.offer_followup_days <= 60:
        raise HTTPException(status_code=422, detail="Werte ungültig")
    payload = data.model_dump()
    return jsonable_encoder(to_ledger_payload(await run(request, lambda conn: finance.save_settings(conn, tenant(), payload, user.email))))


@router.get("/ai/status")
async def ai_status(request: Request, _u: CurrentUser) -> Any:
    from app.core.config import get_settings

    provider = getattr(request.app.state, "ai", None)
    s = get_settings()
    ready, hint = await provider.status() if provider is not None else (False, None)
    return {"active": provider is not None, "ready": ready, "hint": hint, "label": provider.label if provider else None,
            "kind": provider.kind if provider else None, "configured_provider": s.ai_provider, "ollama_model": s.ollama_model}
