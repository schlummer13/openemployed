"""Kundenportal: Anmeldung per E-Mail-Code, Sitzungen, kundenbezogener Datenzugriff, Kündigungseingang.

Sicherheitsparameter (doc 06 §2.2): 6-stelliger Code aus CSPRNG, 10 Minuten gültig, einmalig, neuer Code entwertet
den alten; max. 5 Versuche je Code, max. 10 Fehlversuche je E-Mail/Stunde; max. 3 Codes je E-Mail/15 Min. und
20 je IP/Stunde. Gespeichert wird nur HMAC-SHA-256(Pepper, Wert). Antworten verraten nie, ob eine Adresse bekannt ist.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.services import audit
from app.services.mail import OutgoingMail, WorkspaceMailer
from app.services.masterdata import NotFoundError

CODE_TTL = dt.timedelta(minutes=10)
SESSION_TTL = dt.timedelta(hours=12)
REMEMBER_TTL = dt.timedelta(days=30)
MAX_ATTEMPTS = 5
MAX_FAILURES_PER_HOUR = 10
MAX_CODES_PER_15MIN = 3
MAX_CODES_PER_IP_HOUR = 20


class PortalAuthError(PermissionError):
    pass


class PortalRateLimited(PermissionError):
    pass


class PortalStateError(RuntimeError):
    pass


def _hmac(value: str) -> str:
    pepper = get_settings().session_secret.get_secret_value().encode()
    return hmac.new(pepper, b"portal:" + value.encode(), hashlib.sha256).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def log_event(conn: AsyncConnection, tenant_id: UUID, type_: str, *, email: str | None = None, customer_id: UUID | None = None,
                    subject_id: UUID | None = None, ip: str | None = None, user_agent: str | None = None,
                    data: dict[str, Any] | None = None) -> None:
    import json

    await conn.execute(text(
        "INSERT INTO portal_events (tenant_id, email, customer_id, type, subject_id, ip, user_agent, data) "
        "VALUES (:t, :e, :c, :ty, :s, :ip, :ua, CAST(:d AS jsonb))"),
        {"t": tenant_id, "e": email, "c": customer_id, "ty": type_, "s": subject_id, "ip": ip, "ua": (user_agent or "")[:300],
         "d": json.dumps(data, default=str) if data else None})


# ───────────────────────── Zugänge (intern) ─────────────────────────

async def list_access(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, email, status, created_by, created_at, last_login_at FROM portal_users WHERE tenant_id = :t AND customer_id = :c "
        "ORDER BY email"), {"t": tenant_id, "c": customer_id})
    return [dict(r) for r in rows.mappings()]


async def grant_access(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID, email: str, actor: str) -> None:
    email = normalize_email(email)
    exists = await conn.scalar(text("SELECT 1 FROM customers WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": customer_id})
    if not exists:
        raise NotFoundError("Kunde nicht gefunden")
    await conn.execute(text(
        "INSERT INTO portal_users (tenant_id, customer_id, email, created_by) VALUES (:t, :c, :e, :a) "
        "ON CONFLICT (tenant_id, customer_id, email) DO UPDATE SET status = 'active'"),
        {"t": tenant_id, "c": customer_id, "e": email, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="portal.access_granted", entity_type="customer",
                       entity_id=customer_id, after={"email": email})


async def revoke_access(conn: AsyncConnection, tenant_id: UUID, access_id: UUID, actor: str) -> None:
    row = (await conn.execute(text("UPDATE portal_users SET status = 'disabled' WHERE tenant_id = :t AND id = :id "
                                   "RETURNING customer_id, email"), {"t": tenant_id, "id": access_id})).one_or_none()
    if row is None:
        raise NotFoundError("Zugang nicht gefunden")
    # Sitzungen für diesen Kunden sofort beenden
    await conn.execute(text("UPDATE portal_sessions SET revoked_at = now() WHERE tenant_id = :t AND email = :e "
                            "AND customer_id = :c AND revoked_at IS NULL"), {"t": tenant_id, "e": row.email, "c": row.customer_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="portal.access_revoked", entity_type="customer",
                       entity_id=row.customer_id, after={"email": row.email})


# ───────────────────────── Anmeldung ─────────────────────────

async def _customers_for(conn: AsyncConnection, tenant_id: UUID, email: str) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT c.id, c.number, c.customer_type, COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || "
        "coalesce(c.last_name,''))) AS name FROM portal_users p JOIN customers c ON c.id = p.customer_id "
        "WHERE p.tenant_id = :t AND p.email = :e AND p.status = 'active' AND c.archived_at IS NULL ORDER BY name"),
        {"t": tenant_id, "e": email})
    return [dict(r) for r in rows.mappings()]


async def _employee_for(conn: AsyncConnection, tenant_id: UUID, email: str) -> dict[str, Any] | None:
    from app.services.payroll import employee_for_email

    return await employee_for_email(conn, tenant_id, email)


def _code_mail(email: str, code: str, company: str, portal_url: str) -> OutgoingMail:
    return OutgoingMail(
        to=[email], subject=f"Ihr Anmeldecode: {code} – {company}",
        text="\n".join([
            "Guten Tag,", "", f"Ihr Code für das Portal von {company} lautet:", "", f"    {code}", "",
            "Der Code ist 10 Minuten gültig und kann nur einmal verwendet werden.", f"Portal: {portal_url}", "",
            "Falls Sie keinen Code angefordert haben, können Sie diese Nachricht ignorieren.", "", f"Ihr Team von {company}"]))


async def request_code(conn: AsyncConnection, mailer: WorkspaceMailer | None, tenant_id: UUID, email: str, ip: str | None,
                       company: str) -> str | None:
    """Erzeugt (falls zulässig) einen Code. Rückgabe nur im lokalen Entwicklungsmodus ohne Postfach, sonst ``None``."""
    email = normalize_email(email)
    recent = await conn.scalar(text("SELECT count(*) FROM portal_login_codes WHERE tenant_id = :t AND email = :e "
                                    "AND created_at > now() - interval '15 minutes'"), {"t": tenant_id, "e": email})
    per_ip = await conn.scalar(text("SELECT count(*) FROM portal_login_codes WHERE ip = :ip AND created_at > now() - interval '1 hour'"),
                               {"ip": ip}) if ip else 0
    if recent >= MAX_CODES_PER_15MIN or per_ip >= MAX_CODES_PER_IP_HOUR:
        raise PortalRateLimited("Zu viele Anfragen – bitte in einigen Minuten erneut versuchen")
    customers = await _customers_for(conn, tenant_id, email)
    known = bool(customers) or await _employee_for(conn, tenant_id, email) is not None
    # Auch für unbekannte Adressen wird ein (wertloser) Eintrag geschrieben → gleiches Zeitverhalten und Rate-Limit
    code = f"{secrets.randbelow(1_000_000):06d}"
    await conn.execute(text("UPDATE portal_login_codes SET invalidated_at = now() WHERE tenant_id = :t AND email = :e "
                            "AND used_at IS NULL AND invalidated_at IS NULL"), {"t": tenant_id, "e": email})
    await conn.execute(text(
        "INSERT INTO portal_login_codes (tenant_id, email, code_hash, expires_at, ip) VALUES (:t, :e, :h, now() + :ttl, :ip)"),
        {"t": tenant_id, "e": email, "h": _hmac(f"{email}:{code}") if known else None, "ttl": CODE_TTL, "ip": ip})
    await log_event(conn, tenant_id, "login.code_requested", email=email, ip=ip, data={"known": known})
    if not known:
        return None
    settings = get_settings()
    if mailer is not None:
        await mailer.send(_code_mail(email, code, company, f"{settings.portal_base}/portal"))
        return None
    return code if settings.portal_dev_codes else None


async def verify_code(conn: AsyncConnection, tenant_id: UUID, email: str, code: str, *, remember: bool, ip: str | None,
                      user_agent: str | None) -> tuple[str, list[dict[str, Any]], dt.datetime] | None:
    """``None`` = ungültig (Fehlversuch ist dann bereits gespeichert)."""
    email = normalize_email(email)
    failures = await conn.scalar(text("SELECT count(*) FROM portal_login_failures WHERE tenant_id = :t AND email = :e "
                                      "AND created_at > now() - interval '1 hour'"), {"t": tenant_id, "e": email})
    if failures >= MAX_FAILURES_PER_HOUR:
        raise PortalRateLimited("Zu viele Fehlversuche – bitte später erneut versuchen")
    row = (await conn.execute(text(
        "SELECT id, code_hash, attempts FROM portal_login_codes WHERE tenant_id = :t AND email = :e AND used_at IS NULL "
        "AND invalidated_at IS NULL AND expires_at > now() ORDER BY created_at DESC LIMIT 1 FOR UPDATE"),
        {"t": tenant_id, "e": email})).mappings().one_or_none()
    valid = (row is not None and row["code_hash"] is not None and row["attempts"] < MAX_ATTEMPTS
             and code.isdigit() and len(code) == 6 and hmac.compare_digest(row["code_hash"], _hmac(f"{email}:{code}")))
    if not valid:
        if row is not None:
            await conn.execute(text("UPDATE portal_login_codes SET attempts = attempts + 1, invalidated_at = CASE WHEN attempts + 1 >= :m "
                                    "THEN now() END WHERE id = :id"), {"id": row["id"], "m": MAX_ATTEMPTS})
        await conn.execute(text("INSERT INTO portal_login_failures (tenant_id, email, ip) VALUES (:t, :e, :ip)"),
                           {"t": tenant_id, "e": email, "ip": ip})
        await log_event(conn, tenant_id, "login.failed", email=email, ip=ip)
        # Kein raise: der Fehlversuch muss festgeschrieben werden (ein raise würde die Transaktion zurückrollen)
        return None
    if row is None:  # pragma: no cover – durch ``valid`` ausgeschlossen
        return None
    await conn.execute(text("UPDATE portal_login_codes SET used_at = now() WHERE id = :id"), {"id": row["id"]})
    customers = await _customers_for(conn, tenant_id, email)
    if not customers and await _employee_for(conn, tenant_id, email) is None:  # Zugang inzwischen entzogen
        return None
    token = secrets.token_urlsafe(32)
    expires = dt.datetime.now(dt.UTC) + (REMEMBER_TTL if remember else SESSION_TTL)
    await conn.execute(text(
        "INSERT INTO portal_sessions (tenant_id, token_hash, email, customer_id, expires_at, remember, ip, user_agent) "
        "VALUES (:t, :h, :e, :c, :x, :r, :ip, :ua)"),
        {"t": tenant_id, "h": _hmac(token), "e": email, "c": customers[0]["id"] if len(customers) == 1 else None, "x": expires,
         "r": remember, "ip": ip, "ua": (user_agent or "")[:300]})
    await conn.execute(text("UPDATE portal_users SET last_login_at = now() WHERE tenant_id = :t AND email = :e"),
                       {"t": tenant_id, "e": email})
    await log_event(conn, tenant_id, "login.success", email=email, ip=ip, user_agent=user_agent)
    return token, customers, expires


async def resolve_session(conn: AsyncConnection, tenant_id: UUID, token: str) -> dict[str, Any] | None:
    row = (await conn.execute(text(
        "SELECT id, email, customer_id, remember, expires_at FROM portal_sessions WHERE tenant_id = :t AND token_hash = :h "
        "AND revoked_at IS NULL AND expires_at > now()"), {"t": tenant_id, "h": _hmac(token)})).mappings().one_or_none()
    if row is None:
        return None
    session = dict(row)
    customers = await _customers_for(conn, tenant_id, session["email"])
    employee = await _employee_for(conn, tenant_id, session["email"])
    if not customers and employee is None:
        return None
    if session["customer_id"] is not None and session["customer_id"] not in {c["id"] for c in customers}:
        session["customer_id"] = None
    # rollierende Verlängerung bei „angemeldet bleiben“
    extend = ", expires_at = now() + interval '30 days'" if session["remember"] else ""
    await conn.execute(text(f"UPDATE portal_sessions SET last_seen_at = now(){extend} WHERE id = :id"), {"id": session["id"]})
    session["customers"] = customers
    session["employee"] = employee
    return session


async def select_customer(conn: AsyncConnection, session: dict[str, Any], customer_id: UUID) -> None:
    if customer_id not in {c["id"] for c in session["customers"]}:
        raise PortalAuthError("Kein Zugang zu diesem Kunden")
    await conn.execute(text("UPDATE portal_sessions SET customer_id = :c WHERE id = :id"), {"c": customer_id, "id": session["id"]})


async def logout(conn: AsyncConnection, tenant_id: UUID, session: dict[str, Any], *, everywhere: bool) -> None:
    if everywhere:
        await conn.execute(text("UPDATE portal_sessions SET revoked_at = now() WHERE tenant_id = :t AND email = :e AND revoked_at IS NULL"),
                           {"t": tenant_id, "e": session["email"]})
    else:
        await conn.execute(text("UPDATE portal_sessions SET revoked_at = now() WHERE id = :id"), {"id": session["id"]})


# ───────────────────────── Daten (nur eigener Kunde) ─────────────────────────

async def overview(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID, today: dt.date) -> dict[str, Any]:
    from app.services import receivables as rv
    from app.services.masterdata import current_company_profile, get_customer

    customer = await get_customer(conn, tenant_id, customer_id)
    profile = await current_company_profile(conn, tenant_id, today)
    invoices = (await conn.execute(text(
        "SELECT id, kind, number, issue_date, due_date, total_gross, cancelled_by, cancels_invoice_id FROM invoices "
        "WHERE tenant_id = :t AND customer_id = :c AND status = 'issued' ORDER BY issue_date DESC, number DESC"),
        {"t": tenant_id, "c": customer_id})).mappings().all()
    items = (await rv.open_items(conn, tenant_id, today))["items"]
    open_by_invoice = {i["invoice_id"]: i for i in items if i["customer_id"] == customer_id}
    inv_out = []
    for i in invoices:
        o = open_by_invoice.get(i["id"])
        status = "cancelled" if i["cancelled_by"] or i["kind"] == "cancellation" else (o["status"] if o else "paid")
        inv_out.append(dict(i) | {"status": status, "total_open": o["total_open"] if o else Decimal("0.00")})
    offers = (await conn.execute(text(
        "SELECT id, number, title, status, valid_until, total_net, total_gross, price_mode, sent_at, decided_at FROM offers "
        "WHERE tenant_id = :t AND customer_id = :c AND status IN ('sent', 'accepted', 'declined') ORDER BY sent_at DESC"),
        {"t": tenant_id, "c": customer_id})).mappings().all()
    contracts = (await conn.execute(text(
        "SELECT id, number, title, status, start_date, end_date, notice_received, min_term_months, notice_months, renewal "
        "FROM contracts WHERE tenant_id = :t AND customer_id = :c AND status IN ('active', 'ended') ORDER BY number"),
        {"t": tenant_id, "c": customer_id})).mappings().all()
    notices = (await conn.execute(text(
        "SELECT d.id, d.number, d.level, d.notice_date, d.deadline, d.total, i.number AS invoice_number FROM dunning_notices d "
        "JOIN invoices i ON i.id = d.invoice_id WHERE d.tenant_id = :t AND i.customer_id = :c AND d.sent_at IS NOT NULL "
        "ORDER BY d.notice_date DESC"), {"t": tenant_id, "c": customer_id})).mappings().all()
    terminations = (await conn.execute(text(
        "SELECT number, kind, contract_reference, received_at, status, effective_date FROM contract_terminations "
        "WHERE tenant_id = :t AND customer_id = :c ORDER BY received_at DESC"), {"t": tenant_id, "c": customer_id})).mappings().all()
    return {
        "customer": {"id": customer["id"], "number": customer["number"], "type": customer["customer_type"],
                     "name": customer["company_name"] or f"{customer['first_name'] or ''} {customer['last_name'] or ''}".strip(),
                     "email": customer["email"], "street": customer["street"], "postal_code": customer["postal_code"],
                     "city": customer["city"]},
        "company": {"name": profile["legal_name"] if profile else "", "email": profile["email"] if profile else None,
                    "phone": profile["phone"] if profile else None, "iban": profile["iban"] if profile else None,
                    "bic": profile["bic"] if profile else None, "bank_name": profile["bank_name"] if profile else None},
        "invoices": inv_out, "offers": [dict(o) | {"expired": o["status"] == "sent" and o["valid_until"] < today} for o in offers],
        "contracts": [dict(c) for c in contracts], "notices": [dict(n) for n in notices],
        "terminations": [dict(t) for t in terminations],
        "total_open": sum((i["total_open"] for i in inv_out if i["status"] not in ("paid", "cancelled")), start=Decimal("0.00")),
    }


async def owns(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID, table: str, object_id: UUID) -> None:
    """Prüft, dass das Objekt dem angemeldeten Kunden gehört (sonst 404 – keine Existenzauskunft)."""
    queries = {
        "invoices": "SELECT 1 FROM invoices WHERE tenant_id = :t AND id = :id AND customer_id = :c AND status = 'issued'",
        "offers": "SELECT 1 FROM offers WHERE tenant_id = :t AND id = :id AND customer_id = :c "
                  "AND status IN ('sent','accepted','declined')",
        "contracts": "SELECT 1 FROM contracts WHERE tenant_id = :t AND id = :id AND customer_id = :c",
        "dunning_notices": "SELECT 1 FROM dunning_notices d JOIN invoices i ON i.id = d.invoice_id WHERE d.tenant_id = :t "
                           "AND d.id = :id AND i.customer_id = :c AND d.sent_at IS NOT NULL",
    }
    if not await conn.scalar(text(queries[table]), {"t": tenant_id, "id": object_id, "c": customer_id}):
        raise NotFoundError("Nicht gefunden")


def girocode(company: dict[str, Any], amount: Decimal, reference: str) -> str | None:
    from app.einvoice.pdf import girocode_data_uri

    if not company.get("iban"):
        return None
    return girocode_data_uri({"seller": {"legal_name": company["name"], "iban": company["iban"], "bic": company.get("bic")},
                              "totals": {"gross": amount}}, reference)


# ───────────────────────── Kündigung / Widerruf ─────────────────────────

def _termination_mail(rec: dict[str, Any], company: str) -> OutgoingMail:
    kind = "Kündigung" if rec["kind"] == "termination" else "Widerruf"
    from app.services.invoices import BERLIN

    received = rec["received_at"].astimezone(BERLIN)
    lines = [f"Guten Tag {rec['name']},", "",
             f"hiermit bestätigen wir den Eingang Ihrer {kind} am {received:%d.%m.%Y um %H:%M} Uhr.", "",
             f"Eingangsnummer: {rec['number']}"]
    if rec.get("contract_reference"):
        lines.append(f"Vertrag: {rec['contract_reference']}")
    if rec.get("requested_date"):
        lines.append(f"Gewünschter Zeitpunkt: {rec['requested_date']:%d.%m.%Y}")
    elif rec["kind"] == "termination":
        lines.append("Gewünschter Zeitpunkt: zum nächstmöglichen Termin")
    lines += ["", f"Wir prüfen Ihre {kind} und teilen Ihnen den Zeitpunkt der Wirksamkeit umgehend mit.", "",
              "Diese Bestätigung dient als Nachweis des Zugangs (§ 312k Abs. 4 BGB).", "", "Mit freundlichen Grüßen", company]
    return OutgoingMail(to=[rec["email"]], subject=f"Eingangsbestätigung Ihrer {kind} – {rec['number']}", text="\n".join(lines))


async def submit_termination(conn: AsyncConnection, mailer: WorkspaceMailer | None, tenant_id: UUID, data: dict[str, Any], *,
                             channel: str, ip: str | None, user_agent: str | None, company: str,
                             customer_id: UUID | None = None, contract_id: UUID | None = None) -> dict[str, Any]:
    """Protokolliert die Erklärung sofort (Zugang = Eingang) und sendet die Eingangsbestätigung."""
    email = normalize_email(data["email"])
    if customer_id is None and data.get("customer_number"):
        customer_id = await conn.scalar(text("SELECT id FROM customers WHERE tenant_id = :t AND number = :n AND lower(email) = :e"),
                                        {"t": tenant_id, "n": data["customer_number"].strip().upper(), "e": email})
    if contract_id is None and customer_id and data.get("contract_reference"):
        contract_id = await conn.scalar(text("SELECT id FROM contracts WHERE tenant_id = :t AND customer_id = :c AND number = :n"),
                                        {"t": tenant_id, "c": customer_id, "n": data["contract_reference"].strip().upper()})
    rec = (await conn.execute(text(
        "INSERT INTO contract_terminations (tenant_id, kind, channel, termination_type, contract_id, customer_id, name, email, "
        "customer_number, contract_reference, requested_date, reason, ip, user_agent) VALUES (:t, :k, :ch, :tt, :ct, :cu, :n, :e, "
        ":cn, :cr, :rd, :r, :ip, :ua) RETURNING id, number, kind, name, email, contract_reference, requested_date, received_at"),
        {"t": tenant_id, "k": data["kind"], "ch": channel, "tt": data.get("termination_type", "ordinary"), "ct": contract_id,
         "cu": customer_id, "n": data["name"], "e": email, "cn": data.get("customer_number"), "cr": data.get("contract_reference"),
         "rd": data.get("requested_date"), "r": data.get("reason"), "ip": ip, "ua": (user_agent or "")[:300]})).mappings().one()
    out = dict(rec) | {"confirmation_sent": False}
    if mailer is not None:
        await mailer.send(_termination_mail(out, company))
        await conn.execute(text("UPDATE contract_terminations SET confirmation_sent_at = now() WHERE id = :id"), {"id": rec["id"]})
        out["confirmation_sent"] = True
    await log_event(conn, tenant_id, f"{data['kind']}.received", email=email, customer_id=customer_id, subject_id=rec["id"], ip=ip,
                    user_agent=user_agent)
    return out


async def list_terminations(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT t.id, t.number, t.kind, t.channel, t.termination_type, t.contract_id, t.customer_id, t.name, t.email, "
        "t.customer_number, t.contract_reference, t.requested_date, t.reason, t.received_at, t.confirmation_sent_at, t.status, "
        "t.processed_by, t.processed_at, t.effective_date, t.processing_note, c.number AS contract_number "
        "FROM contract_terminations t LEFT JOIN contracts c ON c.id = t.contract_id WHERE t.tenant_id = :t "
        "ORDER BY (t.status = 'received') DESC, t.received_at DESC LIMIT 500"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def process_termination(conn: AsyncConnection, tenant_id: UUID, termination_id: UUID, *, accept: bool,
                              contract_id: UUID | None, note: str | None, actor: str) -> dict[str, Any]:
    from app.services import contracts as ct
    from app.services.invoices import BERLIN

    rec = (await conn.execute(text("SELECT * FROM contract_terminations WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                              {"t": tenant_id, "id": termination_id})).mappings().one_or_none()
    if rec is None:
        raise NotFoundError("Eingang nicht gefunden")
    if rec["status"] != "received":
        raise PortalStateError("Eingang ist bereits bearbeitet")
    effective = None
    contract_id = contract_id or rec["contract_id"]
    if accept:
        if rec["kind"] == "withdrawal":
            if contract_id is None:
                raise PortalStateError("Bitte den widerrufenen Vertrag zuordnen")
            raise_if_not_draft_or_active = await conn.scalar(text("SELECT status FROM contracts WHERE tenant_id = :t AND id = :id"),
                                                             {"t": tenant_id, "id": contract_id})
            if raise_if_not_draft_or_active is None:
                raise NotFoundError("Vertrag nicht gefunden")
            # Widerruf: Vertrag wird rückabgewickelt – Erstattung/Storno der Rechnungen erfolgt manuell (Hinweis in der Oberfläche)
            await conn.execute(text("UPDATE contracts SET status = 'ended', end_date = :d, updated_at = now() WHERE id = :id"),
                               {"d": rec["received_at"].astimezone(BERLIN).date(), "id": contract_id})
            effective = rec["received_at"].astimezone(BERLIN).date()
        else:
            if contract_id is None:
                raise PortalStateError("Bitte den gekündigten Vertrag zuordnen")
            c = await ct.terminate(conn, tenant_id, contract_id, rec["received_at"].astimezone(BERLIN).date(), actor, rec["requested_date"])
            effective = c["end_date"]
    await conn.execute(text(
        "UPDATE contract_terminations SET status = :s, processed_by = :a, processed_at = now(), effective_date = :e, "
        "processing_note = :n, contract_id = :c WHERE id = :id"),
        {"s": "processed" if accept else "rejected", "a": actor, "e": effective, "n": note, "c": contract_id, "id": termination_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="termination.processed" if accept else "termination.rejected",
                       entity_type="contract_termination", entity_id=termination_id, after={"effective": effective, "note": note})
    return {"effective_date": effective}


# ───────────────────────── Willkommensmail ─────────────────────────

def welcome_mail(customer: dict[str, Any], company: dict[str, Any], portal_url: str) -> OutgoingMail:
    b2b = customer["customer_type"] == "b2b"
    name = customer.get("company_name") or f"{customer.get('first_name') or ''} {customer.get('last_name') or ''}".strip()
    salutation = "Guten Tag" if b2b else f"Guten Tag {name}"
    lines = [
        f"{salutation},", "",
        f"herzlich willkommen bei {company['name']}! Wir freuen uns auf die Zusammenarbeit.", "",
        "Für Sie ist ab sofort unser Kundenportal eingerichtet. Dort finden Sie jederzeit:",
        "  • Ihre Rechnungen (PDF und E-Rechnung) und den aktuellen Zahlungsstand",
        "  • Zahlungsinformationen mit QR-Code für Ihre Banking-App",
        "  • Angebote – mit der Möglichkeit, sie direkt online anzunehmen",
        "  • Ihre Verträge inklusive Kündigungsmöglichkeit", "",
        f"Portal: {portal_url}",
        f"Anmeldung: Geben Sie einfach Ihre E-Mail-Adresse ({customer['email']}) ein – Sie erhalten dann einen",
        "einmaligen Anmeldecode per E-Mail. Ein Passwort ist nicht nötig.", "",
        f"Ihre Kundennummer: {customer['number']}", "",
        "Bei Fragen antworten Sie einfach auf diese E-Mail.", "", "Mit freundlichen Grüßen", company["name"],
    ]
    return OutgoingMail(to=[customer["email"]], subject=f"Willkommen bei {company['name']} – Ihr Kundenportal",
                        text="\n".join(lines), reply_to=company.get("email") or None)


async def send_welcome(conn: AsyncConnection, mailer: WorkspaceMailer | None, tenant_id: UUID, customer_id: UUID, actor: str,
                       today: dt.date) -> dict[str, Any]:
    """Portalzugang für die Kunden-E-Mail freischalten und Willkommensmail senden (idempotent beim Zugang)."""
    from app.services.masterdata import current_company_profile, get_customer

    customer = dict(await get_customer(conn, tenant_id, customer_id))
    await grant_access(conn, tenant_id, customer_id, customer["email"], actor)
    if mailer is None:
        return {"access": True, "mail_sent": False, "reason": "E-Mail nicht eingerichtet"}
    profile = await current_company_profile(conn, tenant_id, today)
    company = {"name": profile["legal_name"] if profile else "Ihr Dienstleister", "email": profile["email"] if profile else None}
    await mailer.send(welcome_mail(customer, company, f"{get_settings().portal_base}/portal"))
    await conn.execute(text("UPDATE customers SET welcome_sent_at = now() WHERE id = :id"), {"id": customer_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="customer.welcome_sent", entity_type="customer",
                       entity_id=customer_id, after={"to": customer["email"]})
    return {"access": True, "mail_sent": True}
