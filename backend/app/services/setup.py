"""Einrichtungs-Checkliste für den Produktivstart: automatisch geprüfte und manuell bestätigte Schritte."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.services import audit
from app.services.masterdata import NotFoundError

MANUAL = {
    "procedure_doc": ("Verfahrensdokumentation freigegeben",
                      "Mit dem Steuerberater durchgehen und unterschreiben (docs/verfahrensdokumentation.md)."),
    "offsite_backup": ("Datensicherung außer Haus",
                       "Tägliche Sicherung zusätzlich auf eine Storage Box kopieren und Wiederherstellung testen."),
    "tax_advisor": ("Kontenrahmen mit Steuerberater abgestimmt",
                    "SKR03/SKR04, BU-Schlüssel und Standardkonten der Lieferanten prüfen lassen."),
}


async def status(conn: AsyncConnection, tenant_id: UUID, today: dt.date, ai_ready: bool) -> dict[str, Any]:
    from app.services import masterdata as md
    from app.services import team

    s = get_settings()
    profile = await md.current_company_profile(conn, tenant_id, today)
    holders = await team.list_shareholders(conn, tenant_id, today)
    mds = [h for h in holders if h["is_managing_director"]]
    tax_ok = sum(1 for h in mds if h.get("tax_profile") and h.get("tax_id"))
    sup = (await conn.execute(text(
        "SELECT count(*) AS total, count(*) FILTER (WHERE default_account IS NOT NULL AND NOT needs_review) AS ruled, "
        "count(*) FILTER (WHERE needs_review) AS review FROM suppliers WHERE tenant_id = :t AND archived_at IS NULL"),
        {"t": tenant_id})).mappings().one()
    bank = await conn.scalar(text("SELECT count(*) FROM bank_connections WHERE tenant_id = :t"), {"t": tenant_id})
    flags = {r.key: r for r in await conn.execute(text("SELECT key, done_at, done_by FROM setup_flags WHERE tenant_id = :t"),
                                                  {"t": tenant_id})}
    items: list[dict[str, Any]] = [
        {"key": "company", "label": "Firmenprofil vollständig", "done": profile is not None and not md.missing_invoice_fields(profile),
         "hint": "Pflichtangaben für Rechnungen (§ 14 UStG, § 35a GmbHG)", "href": "/einstellungen/firma"},
        {"key": "shareholders", "label": "Steuerdaten der Geschäftsführer", "done": bool(mds) and tax_ok == len(mds),
         "hint": f"{tax_ok} von {len(mds)} mit Steuer-ID und Lohnsteuerdaten", "href": "/gehaelter"},
        {"key": "supplier_rules", "label": "Lieferanten-Regeln festgelegt",
         "done": sup["total"] > 0 and sup["review"] == 0 and sup["ruled"] > 0,
         "hint": f"{sup['ruled']} von {sup['total']} Lieferanten mit Standardkonto" + (f", {sup['review']} neu" if sup["review"] else ""),
         "href": "/belege"},
        {"key": "bank", "label": "Bankkonto angebunden", "done": bool(bank), "hint": "Kontoauszug-Import oder Onlinebanking (FinTS)",
         "href": "/buchhaltung"},
        {"key": "fints", "label": "FinTS-Produkt-ID hinterlegt", "done": bool(s.fints_product_id),
         "hint": "Bei der Deutschen Kreditwirtschaft beantragen, dann FINTS_PRODUCT_ID in der .env", "href": None},
        {"key": "google_login", "label": "Anmeldeverfahren eingerichtet",
         "done": (s.google_login_configured or s.microsoft_login_configured or s.oidc_login_configured or s.auth_password_enabled)
         and not s.dev_login_enabled,
         "hint": "Google, Microsoft, OIDC oder E-Mail + Passwort; Entwickler-Anmeldung abschalten", "href": "/einstellungen/system"},
        {"key": "mail", "label": "E-Mail-Versand eingerichtet", "done": s.mail_configured and s.mail_auth_mode != "local",
         "hint": "Microsoft 365, Google, IONOS, STRATO … statt Test-Postfach", "href": "/einrichtung"},
        {"key": "ai", "label": "Lokale KI für die Belegerkennung bereit", "done": ai_ready, "hint": "Ollama mit Qwen 2.5 VL",
         "href": "/automatisierungen"},
    ]
    for key, (label, hint) in MANUAL.items():
        f = flags.get(key)
        items.append({"key": key, "label": label, "done": f is not None, "hint": hint, "href": None, "manual": True,
                      "done_by": f.done_by if f else None, "done_at": f.done_at if f else None})
    done = sum(1 for i in items if i["done"])
    return {"items": items, "done": done, "total": len(items)}


async def set_flag(conn: AsyncConnection, tenant_id: UUID, key: str, done: bool, actor: str) -> None:
    if key not in MANUAL:
        raise NotFoundError("Unbekannter Schritt")
    if done:
        await conn.execute(text("INSERT INTO setup_flags (tenant_id, key, done_by) VALUES (:t, :k, :a) ON CONFLICT DO NOTHING"),
                           {"t": tenant_id, "k": key, "a": actor})
    else:
        await conn.execute(text("DELETE FROM setup_flags WHERE tenant_id = :t AND key = :k"), {"t": tenant_id, "k": key})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="setup.flag", entity_type="setup", entity_id=None,
                       after={"key": key, "done": done})
