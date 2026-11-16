"""ELSTER-Übertragung der Umsatzsteuer-Voranmeldung: Datensatz erzeugen, mit ERiC prüfen und senden, protokollieren."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.domain.elster import ElsterError, elster_tax_number, ustva_xml
from app.services import audit
from app.services.eric import EricClient, EricNotInstalled, library_path
from app.services.invoices import today_berlin
from app.services.masterdata import ConflictError


def status() -> dict[str, Any]:
    s = get_settings()
    return {"eric_installed": library_path(s.eric_home) is not None, "hersteller_id": bool(s.elster_hersteller_id),
            "certificate": Path(s.elster_cert_path).exists(), "test_mode": s.elster_test_mode, "state": s.company_state}


async def build_xml(conn: AsyncConnection, tenant_id: UUID, spec: str, *, test: bool) -> tuple[str, dict[str, Any]]:
    from app.services import tax_report
    from app.services.masterdata import current_company_profile

    s = get_settings()
    profile = await current_company_profile(conn, tenant_id, today_berlin())
    if profile is None or not profile["tax_number"]:
        raise ConflictError("Steuernummer im Firmenprofil fehlt")
    data = await tax_report.ustva(conn, tenant_id, spec)
    try:
        elster_tax_number(profile["tax_number"], s.company_state)
        xml = ustva_xml(spec=spec, lines=data["lines"], tax_number=profile["tax_number"], state=s.company_state,
                        company={"name": profile["legal_name"], "street": profile["street"], "postal_code": profile["postal_code"],
                                 "city": profile["city"]}, hersteller_id=s.elster_hersteller_id or "00000", test=test,
                        created=today_berlin())
    except ElsterError as exc:
        raise ConflictError(str(exc)) from exc
    return xml, data


async def _record(conn: AsyncConnection, tenant_id: UUID, spec: str, test: bool, status_: str, xml: str, actor: str, *,
                  ticket: str | None = None, response: str | None = None, error: str | None = None,
                  kind: str = "ustva") -> None:
    await conn.execute(text(
        "INSERT INTO elster_submissions (tenant_id, kind, period, test_mode, status, transfer_ticket, xml_sha256, response, error, "
        "created_by) VALUES (:t, :k, :p, :tm, :s, :tt, :h, :r, :e, :a)"),
        {"t": tenant_id, "k": kind, "p": spec, "tm": test, "s": status_, "tt": ticket, "h": hashlib.sha256(xml.encode()).hexdigest(),
         "r": (response or "")[:20000] or None, "e": (error or "")[:4000] or None, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"elster.{status_}", entity_type=kind, entity_id=spec,
                       after={"test": test, "ticket": ticket})


def _client() -> EricClient:
    try:
        return EricClient(get_settings().eric_home)
    except EricNotInstalled as exc:
        raise ConflictError(str(exc) + " – Einrichtung siehe docs/betrieb.md (ELSTER)") from exc


async def validate(conn: AsyncConnection, tenant_id: UUID, spec: str, actor: str) -> dict[str, Any]:
    s = get_settings()
    xml, _ = await build_xml(conn, tenant_id, spec, test=True)
    result = await _client().validate(xml, f"UStVA_{spec[:4]}")
    await _record(conn, tenant_id, spec, True, "validated" if result.ok else "error", xml, actor, response=result.response,
                  error=result.error)
    return {"ok": result.ok, "error": result.error, "response": result.response, "test_mode": s.elster_test_mode}


async def send(conn: AsyncConnection, ledger: Any, tenant_id: UUID, spec: str, pin: str, actor: str) -> dict[str, Any]:
    """Versand nur für festgeschriebene Zeiträume (die übermittelten Werte entsprechen den festgeschriebenen)."""
    from app.services.tax_report import months_for

    s = get_settings()
    if not s.elster_hersteller_id:
        raise ConflictError("ELSTER_HERSTELLER_ID fehlt (Registrierung im ELSTER-Entwicklerbereich)")
    if not Path(s.elster_cert_path).exists():
        raise ConflictError("ELSTER-Zertifikat (.pfx) nicht gefunden")
    locked = set(await ledger.locked_periods(tenant_id=tenant_id))
    if any(m not in locked for m in months_for(spec)):
        raise ConflictError("Bitte den Zeitraum zuerst festschreiben (Steuern → Voranmeldung → Festschreiben)")
    xml, _ = await build_xml(conn, tenant_id, spec, test=s.elster_test_mode)
    result = await _client().send(xml, f"UStVA_{spec[:4]}", s.elster_cert_path, pin)
    await _record(conn, tenant_id, spec, s.elster_test_mode, "sent" if result.ok else "error", xml, actor,
                  ticket=result.transfer_ticket, response=result.server_response or result.response, error=result.error)
    return {"ok": result.ok, "transfer_ticket": result.transfer_ticket, "error": result.error, "test_mode": s.elster_test_mode}


async def history(conn: AsyncConnection, tenant_id: UUID, spec: str, kind: str = "ustva") -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, kind, period, test_mode, status, transfer_ticket, error, created_by, created_at FROM elster_submissions "
        "WHERE tenant_id = :t AND period = :p AND kind = :k ORDER BY created_at DESC"), {"t": tenant_id, "p": spec, "k": kind})
    return [dict(r) for r in rows.mappings()]
