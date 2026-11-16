"""Automatisierungen: zeitgesteuerte Jobs mit Schalter, Laufprotokoll und Sperre, plus Aufgabenliste.

Jeder Job läuft höchstens einmal gleichzeitig (Advisory-Lock je Job), protokolliert Start/Ende/Ergebnis in
``automation_runs`` und kann einzeln abgeschaltet oder manuell gestartet werden. Steuerlich relevante Schritte
(Buchen, Festschreiben) werden nie automatisch ausgelöst – Automatisierungen erzeugen Entwürfe, Zuordnungen bei
eindeutiger Lage und Aufgaben.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import get_settings
from app.services.invoices import BERLIN

log = logging.getLogger("selbstlite.automation")
D = Decimal


@dataclass
class Context:
    engine: AsyncEngine
    ledger: Any
    mailer: Any
    extractor: Any
    tenant_id: UUID
    actor: str


@dataclass(frozen=True)
class Job:
    key: str
    label: str
    description: str
    schedule: str            # "daily@6", "every@15", "weekly@0@8" (Wochentag 0 = Montag)
    schedule_label: str
    run: Callable[[Context, dt.date], Awaitable[tuple[str, str, dict[str, Any]]]]
    available: Callable[[Context], str | None] = lambda ctx: None
    default_enabled: bool = True


# ───────────────────────── Jobs ─────────────────────────

async def _billing(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services.contracts import run_billing

    r = await run_billing(ctx.engine, ctx.ledger, ctx.tenant_id, today, ctx.actor)
    if r.get("skipped"):
        return "skipped", r.get("reason", "läuft bereits"), r
    issued = sum(1 for c in r["created"] if c.get("status") == "issued")
    return ("error" if r["errors"] else "ok"), f"{len(r['created'])} Rechnungen ({issued} ausgestellt), {len(r['errors'])} Fehler", \
        {"created": r["created"], "errors": r["errors"]}


async def _dunning(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services.receivables import run_dunning

    r = await run_dunning(ctx.engine, ctx.mailer, ctx.tenant_id, today, ctx.actor)
    if r.get("skipped"):
        return "skipped", r.get("reason", "läuft bereits"), r
    return ("error" if r["errors"] else "ok"), f"{len(r['created'])} Schreiben erstellt, {len(r['sent'])} versendet", r


async def _auto_match(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services import bank as bk

    async with ctx.engine.begin() as conn:
        n = await bk.auto_match(conn, ctx.ledger, ctx.tenant_id, ctx.actor, today)
    return "ok", f"{n} Zahlungen zugeordnet", {"matched": n}


ALLOWED_ATTACHMENTS = {"application/pdf", "image/png", "image/jpeg", "application/xml", "text/xml"}


async def _receipt_inbox(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    """Belegpostfach: Anhänge abholen → Datei speichern → erkennen → Belegentwurf (nie buchen)."""
    from app.services import receipts as rc
    from app.services.masterdata import current_company_profile

    mails = await ctx.mailer.fetch_unprocessed(limit=25)
    stats: dict[str, Any] = {"mails": 0, "files": 0, "drafts": 0, "notes": []}
    for mail in mails:
        msg_id = (mail.message_id or f"uid:{mail.uid}")[:300]
        files = drafts = 0
        notes: list[str] = []
        async with ctx.engine.begin() as conn:
            seen = await conn.scalar(text("SELECT 1 FROM receipt_inbox_messages WHERE tenant_id = :t AND message_id = :m"),
                                     {"t": ctx.tenant_id, "m": msg_id})
            if not seen:
                profile = await current_company_profile(conn, ctx.tenant_id, today)
                company = profile["legal_name"] if profile else ""
                for att in mail.attachments:
                    ctype = "application/xml" if att.filename.lower().endswith(".xml") else att.content_type
                    if ctype not in ALLOWED_ATTACHMENTS or not att.data:
                        continue
                    try:
                        stored = await rc.store_file(conn, ctx.tenant_id, att.data, att.filename[:200] or "beleg", ctx.actor)
                    except (rc.ReceiptError, ValueError) as exc:
                        notes.append(f"{att.filename}: {exc}")
                        continue
                    files += 1
                    if stored.get("duplicate_of_receipt"):
                        notes.append(f"{att.filename}: bereits als Beleg erfasst")
                        continue
                    draft = await _draft_from_file(conn, ctx, stored["id"], company, notes, att.filename)
                    drafts += 1 if draft else 0
                await conn.execute(text(
                    "INSERT INTO receipt_inbox_messages (tenant_id, message_id, sender, subject, files, drafts, note) "
                    "VALUES (:t, :m, :s, :sub, :f, :d, :n)"),
                    {"t": ctx.tenant_id, "m": msg_id, "s": mail.sender[:300], "sub": mail.subject[:300], "f": files, "d": drafts,
                     "n": "; ".join(notes)[:2000] or None})
        await ctx.mailer.mark_processed(mail.uid)
        stats["mails"] += 1
        stats["files"] += files
        stats["drafts"] += drafts
        stats["notes"] += notes
    return "ok", f"{stats['mails']} Mails, {stats['files']} Dateien, {stats['drafts']} Belegentwürfe", stats


async def _draft_from_file(conn: AsyncConnection, ctx: Context, file_id: UUID, company: str, notes: list[str],
                           name: str, source: str = "inbox") -> bool:
    from app.services import extraction as ex
    from app.services import receipts as rc

    try:
        # Sicherungspunkt: ein fehlerhafter Beleg darf die übrigen Anhänge der Mail nicht zurückrollen
        async with conn.begin_nested():
            sug = await ex.extract_file(conn, ctx.extractor, ctx.tenant_id, file_id, ctx.actor, company)
            if not sug.get("supplier_name") or not sug.get("net_amount") or not sug.get("receipt_date"):
                notes.append(f"{name}: Daten unvollständig – bitte manuell erfassen")
                return False
            supplier_id = sug.get("supplier_id")
            if supplier_id is None:
                # Neuer Lieferant: wird angelegt, aber erst nach der ersten manuellen Einsortierung automatisch kontiert
                created = await rc.save_supplier(conn, ctx.tenant_id, {
                    "name": sug["supplier_name"], "country_code": sug["supplier_country"] or "DE",
                    "vat_id": sug["supplier_vat_id"] if sug.get("supplier_vat_id") and len(sug["supplier_vat_id"]) <= 14 else None,
                    "default_tax_case": sug["tax_case"], "default_category": sug["category"], "notes": "Automatisch aus dem Belegpostfach"},
                    ctx.actor, needs_review=True)
                supplier_id = created["id"]  # RowMapping
            draft = await rc.save_draft(conn, ctx.tenant_id, {
                "supplier_id": supplier_id, "file_id": file_id, "receipt_date": dt.date.fromisoformat(sug["receipt_date"]),
                "service_date": dt.date.fromisoformat(sug["service_date"]) if sug.get("service_date") else None,
                "invoice_number": sug.get("invoice_number"), "description": sug["description"][:500], "category": sug["category"],
                "tax_case": sug["tax_case"], "net_amount": D(sug["net_amount"]), "tax_amount": D(sug["tax_amount"] or "0"),
                "paid_on": None, "expense_account": sug.get("expense_account")}, ctx.actor)
            await conn.execute(text("UPDATE receipts SET source = :src WHERE id = :id"), {"src": source, "id": draft["id"]})
            return True
    except ex.ExtractionNotConfigured:
        notes.append(f"{name}: KI nicht eingerichtet – Datei liegt zur manuellen Erfassung bereit")
    except Exception as exc:
        notes.append(f"{name}: {exc}")
    return False


async def _postings(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services.accounting import ensure_postings

    async with ctx.engine.begin() as conn:
        n = await ensure_postings(conn, ctx.ledger, ctx.tenant_id, ctx.actor)
    return "ok", f"{n} Buchungssätze erzeugt", {"created": n}


async def _ledger_verify(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    """Prüft die gesamte Hash-Kette der Festschreibungen. Bei einem Bruch: Fehlerstatus, Aufgabe und E-Mail-Alarm."""
    report = await ctx.ledger.verify()
    if report.ok:
        return "ok", f"Hash-Kette intakt ({report.records_checked} Festschreibungen geprüft)", {"records": report.records_checked}
    if ctx.mailer is not None:
        from app.services.mail import OutgoingMail

        async with ctx.engine.connect() as conn:
            emails = [r[0] for r in await conn.execute(text("SELECT email FROM shareholders WHERE tenant_id = :t AND email IS NOT NULL"),
                                                        {"t": ctx.tenant_id})]
        recipients = sorted(set(emails) | set(get_settings().allowed_emails))
        if recipients:
            await ctx.mailer.send(OutgoingMail(to=recipients, subject="ALARM OpenEmployed: Hash-Kette der Festschreibungen gebrochen",
                                               text=f"Die tägliche Integritätsprüfung hat eine Abweichung festgestellt:\n\n{report}\n\n"
                                                    "Bitte sofort den Steuerberater informieren und keine weiteren Daten ändern. "
                                                    "Die Datenbank-Sicherung des Vortags aufbewahren."))
    return "error", f"HASH-KETTE GEBROCHEN: {report}"[:900], {"report": str(report)}


async def _archive(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services import archive

    async with ctx.engine.begin() as conn:
        counts = await archive.sync(conn, ctx.tenant_id, actor="system:archiv")
    total = sum(counts.values())
    return "ok", f"{total} Dokumente archiviert" if total else "Archiv aktuell", counts


async def _archive_replication(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services import archive_sync

    async with ctx.engine.begin() as conn:
        res = await archive_sync.run_scheduled(conn, ctx.tenant_id, today)
    if not res["chain"]["ok"]:
        return "error", "Archiv-Hash-Kette fehlerhaft: " + "; ".join(res["chain"]["problems"][:3]), res
    bad = [n for n, t in res["targets"].items() if t.get("status") == "error" or not t.get("verify", {}).get("ok", True)]
    if bad:
        return "error", "Spiegelung fehlerhaft: " + ", ".join(bad), res
    parts = [f"{n}: {t.get('message')}" for n, t in res["targets"].items()]
    return "ok", f"Kette ok ({res['chain']['checked']} Dokumente)" + (" · " + "; ".join(parts) if parts else ""), res


async def _documenso_sync(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.main import app
    from app.services import documenso_setup, sign_channels

    async with ctx.engine.begin() as conn:
        qes_counts = await sign_channels.sync_qes(conn, ctx.tenant_id)
    client = getattr(app.state, "documenso", None)
    if client is None or not hasattr(client, "list_documents"):
        return "skipped", f"Documenso nicht verbunden; QES: {qes_counts['signed']} unterschrieben", qes_counts
    async with ctx.engine.begin() as conn:
        counts = await documenso_setup.sync(conn, client, ctx.ledger, ctx.tenant_id)
    counts["archived"] += qes_counts["signed"]
    total = sum(counts.values())
    summary = f"{counts['imported']} neu, {counts['updated']} aktualisiert, {counts['archived']} archiviert" if total else "aktuell"
    return "ok", summary, counts


async def _payroll_reports(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services.payroll_reports import refresh_sv_reports

    async with ctx.engine.begin() as conn:
        n = await refresh_sv_reports(conn, ctx.tenant_id, today)
    return "ok", f"{n} SV-Meldung(en) neu oder aktualisiert" if n else "aktuell", {"changed": n}


async def _webhooks(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services import webhooks

    stats = await webhooks.deliver_due(ctx.engine)
    if not stats["delivered"] and not stats["failed"]:
        return "ok", "nichts zuzustellen", stats
    return ("error" if stats["failed"] else "ok"), f"{stats['delivered']} zugestellt, {stats['failed']} endgültig fehlgeschlagen", stats


async def _workflows(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services import workflows

    stats = await workflows.process_queue(ctx, dt.datetime.now(BERLIN))
    ran = stats["succeeded"] + stats["stopped"] + stats["failed"]
    if not ran:
        return "ok", "keine Läufe", stats
    return ("error" if stats["failed"] else "ok"), f"{ran} Läufe, {stats['failed']} fehlgeschlagen", stats


async def _exchange_rates(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services import exchange

    async with ctx.engine.begin() as conn:
        if not await conn.scalar(text("SELECT 1 FROM customers WHERE currency <> 'EUR' LIMIT 1")):
            return "ok", "keine Fremdwährungskunden", {}
        n = await exchange.fetch_ecb(conn)
    return "ok", f"{n} EZB-Kurse aktualisiert", {"rates": n}


async def _cleanup(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    """Datensparsamkeit: abgelaufene Portal-Codes, Sitzungen und alte Fehlversuche löschen."""
    async with ctx.engine.begin() as conn:
        codes = (await conn.execute(text("DELETE FROM portal_login_codes WHERE created_at < now() - interval '7 days'"))).rowcount
        sessions = (await conn.execute(text("DELETE FROM portal_sessions WHERE expires_at < now() - interval '7 days' "
                                            "OR revoked_at < now() - interval '7 days'"))).rowcount
        fails = (await conn.execute(text("DELETE FROM portal_login_failures WHERE created_at < now() - interval '7 days'"))).rowcount
        runs = (await conn.execute(text("DELETE FROM automation_runs WHERE started_at < now() - interval '180 days'"))).rowcount
    return "ok", f"{codes} Codes, {sessions} Sitzungen, {fails} Fehlversuche, {runs} alte Protokolle entfernt", {}


async def _digest(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    from app.services.mail import OutgoingMail
    from app.services.masterdata import current_company_profile

    async with ctx.engine.connect() as conn:
        tasks = await open_tasks(conn, ctx.tenant_id, today, ctx.ledger)
        emails = [r[0] for r in await conn.execute(text("SELECT email FROM shareholders WHERE tenant_id = :t AND email IS NOT NULL"),
                                                    {"t": ctx.tenant_id})]
        profile = await current_company_profile(conn, ctx.tenant_id, today)
        kpi = await kpis(conn, ctx.tenant_id, today)
    recipients = sorted(set(emails) | set(get_settings().allowed_emails))
    if not recipients:
        return "skipped", "keine Empfänger (Gesellschafter-E-Mails hinterlegen)", {}
    lines = [f"Wochenüberblick {today:%d.%m.%Y} – {profile['legal_name'] if profile else 'OpenEmployed'}", "",
             f"Offene Forderungen: {kpi['open_receivables']} € (davon überfällig {kpi['overdue']} €)",
             f"MRR: {kpi['mrr']} €", ""]
    lines += ["Offene Aufgaben:"] + [f"  • {t['title']}{' – ' + t['detail'] if t.get('detail') else ''}" for t in tasks] if tasks \
        else ["Keine offenen Aufgaben."]
    lines += ["", f"{get_settings().app_origin}"]
    await ctx.mailer.send(OutgoingMail(to=recipients, subject=f"OpenEmployed: Wochenüberblick {today:%d.%m.%Y}", text="\n".join(lines)))
    return "ok", f"an {len(recipients)} Empfänger, {len(tasks)} Aufgaben", {"recipients": len(recipients)}


async def _monthly_report(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    """Monatsbericht an die Geschäftsführung: Ergebnis Vormonat, USt-Zahllast, Liquidität, offene Abschlusspunkte."""
    from app.services import closing, finance, tax_report
    from app.services.mail import OutgoingMail

    prev = today.replace(day=1) - dt.timedelta(days=1)
    month = f"{prev:%Y-%m}"
    async with ctx.engine.connect() as conn:
        months = await finance.monthly(conn, ctx.tenant_id, prev, 1)
        cap = await finance.capital(conn, ctx.tenant_id, today, ctx.ledger)
        vat = await tax_report.ustva(conn, ctx.tenant_id, month)
        items = await closing.open_items(conn, ctx.tenant_id, [month])
        emails = [r[0] for r in await conn.execute(text("SELECT email FROM shareholders WHERE tenant_id = :t AND email IS NOT NULL"),
                                                    {"t": ctx.tenant_id})]
    recipients = sorted(set(emails) | set(get_settings().allowed_emails))
    if not recipients:
        return "skipped", "keine Empfänger", {}
    m = months[-1]

    def eur(v: Any) -> str:
        return f"{Decimal(v):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    lines = [f"Monatsbericht {prev:%m/%Y}", "", f"Einnahmen (netto): {eur(m['income'])}",
             f"Ausgaben (inkl. Gehälter): {eur(m['expenses'])}",
             f"Ergebnis: {eur(m['result'])}", f"Zahlungseingänge: {eur(m['cash_in'])}", "",
             f"USt-Zahllast {month}: {eur(vat['payable'])} (Voranmeldung + Festschreibung, Frist beachten)", "",
             f"Kontostand: {eur(cap['balance']['amount'])}{' (geschätzt)' if cap['balance']['estimated'] else ''}",
             f"Gebunden (USt, Ertragsteuern, Verbindlichkeiten, Gehälter/Fixkosten 30 Tage): {eur(cap['reserved'])}",
             f"Frei verfügbar: {eur(cap['available'])}" + ("  ⚠ unter Mindestreserve" if cap["below_minimum"] else ""), ""]
    open_lines = ["Offen für den Monatsabschluss:"] + [f"  • {i['text']}" for i in items]
    lines += open_lines if items else ["Monatsabschluss: keine offenen Punkte."]
    await ctx.mailer.send(OutgoingMail(to=recipients, subject=f"OpenEmployed: Monatsbericht {prev:%m/%Y}", text="\n".join(lines)))
    return "ok", f"Monatsbericht {prev:%m/%Y} an {len(recipients)} Empfänger", {}


async def _liquidity_alert(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    """Warnt per E-Mail, wenn das verfügbare Kapital unter die Mindestreserve fällt (höchstens einmal pro Woche)."""
    from app.services import finance
    from app.services.mail import OutgoingMail

    async with ctx.engine.connect() as conn:
        cap = await finance.capital(conn, ctx.tenant_id, today, ctx.ledger)
        last_alert = await conn.scalar(text("SELECT max(started_at) FROM automation_runs WHERE tenant_id = :t AND key = 'liquidity' "
                                            "AND summary LIKE 'Warnung%'"), {"t": ctx.tenant_id})
        emails = [r[0] for r in await conn.execute(text("SELECT email FROM shareholders WHERE tenant_id = :t AND email IS NOT NULL"),
                                                    {"t": ctx.tenant_id})]
    if not cap["below_minimum"]:
        return "ok", f"verfügbar {cap['available']} € – über der Mindestreserve", {}
    if last_alert and dt.datetime.now(dt.UTC) - last_alert < dt.timedelta(days=7):
        return "ok", "unter Mindestreserve – Warnung bereits in den letzten 7 Tagen versendet", {}
    recipients = sorted(set(emails) | set(get_settings().allowed_emails))
    if recipients and ctx.mailer is not None:
        await ctx.mailer.send(OutgoingMail(to=recipients, subject="OpenEmployed: Liquidität unter Mindestreserve", text=(
            f"Frei verfügbar sind nur noch {cap['available']} € (Mindestreserve {cap['min_liquidity']} €).\n"
            f"Kontostand {cap['balance']['amount']} €, gebunden {cap['reserved']} € (USt {cap['vat_due']} €, Ertragsteuern "
            f"{cap['income_tax_reserve']} €, Verbindlichkeiten {cap['payables']} €, "
            f"Gehälter/Fixkosten {cap['salaries_30d'] + cap['fixed_costs_30d']} €).\n\n"
            "Empfehlung: offene Forderungen einziehen, Ausgaben prüfen, ggf. Gehaltsauszahlung abstimmen.")))
    return "ok", f"Warnung: verfügbar {cap['available']} € unter Mindestreserve", {}


async def _offer_reminder(ctx: Context, today: dt.date) -> tuple[str, str, dict[str, Any]]:
    """Freundliche Erinnerung an Kunden bei offenen Angeboten (einmalig, nach X Tagen, nur innerhalb der Bindefrist)."""
    from app.services import finance
    from app.services.mail import OutgoingMail
    from app.services.masterdata import current_company_profile

    sent = 0
    async with ctx.engine.begin() as conn:
        fs = await finance.settings(conn, ctx.tenant_id)
        profile = await current_company_profile(conn, ctx.tenant_id, today)
        company = profile["legal_name"] if profile else ""
        rows = (await conn.execute(text(
            "SELECT o.id, o.number, o.title, o.valid_until, c.email, c.customer_type, c.company_name, c.first_name, c.last_name, "
            "EXISTS (SELECT 1 FROM portal_users p WHERE p.customer_id = c.id AND p.status = 'active') AS portal "
            "FROM offers o JOIN customers c ON c.id = o.customer_id WHERE o.tenant_id = :t AND o.status = 'sent' "
            "AND o.reminder_sent_at IS NULL AND o.sent_at < :d AND o.valid_until > :today FOR UPDATE OF o"),
            {"t": ctx.tenant_id, "d": today - dt.timedelta(days=int(fs["offer_followup_days"])), "today": today})).mappings().all()
        for o in rows:
            name = o["company_name"] or f"{o['first_name'] or ''} {o['last_name'] or ''}".strip()
            text_lines = ["Guten Tag" + ("" if o["customer_type"] == "b2b" else f" {name}") + ",", "",
                          f"wir möchten kurz an unser Angebot {o['number']} „{o['title']}“ erinnern. Es ist noch bis zum "
                          f"{o['valid_until']:%d.%m.%Y} gültig.",
                          (f"Sie können es bequem im Kundenportal annehmen: {get_settings().portal_base}/portal" if o["portal"]
                           else "Für eine Annahme genügt eine kurze Antwort auf diese E-Mail."),
                          "Haben Sie Fragen oder Änderungswünsche? Wir passen das Angebot gerne an.", "",
                          "Mit freundlichen Grüßen", company]
            await ctx.mailer.send(OutgoingMail(to=[o["email"]], subject=f"Erinnerung: Angebot {o['number']} – {company}",
                                               text="\n".join(text_lines),
                                               reply_to=profile["email"] if profile and profile["email"] else None))
            await conn.execute(text("UPDATE offers SET reminder_sent_at = now() WHERE id = :id"), {"id": o["id"]})
            sent += 1
    return "ok", f"{sent} Erinnerung(en) versendet", {"sent": sent}


def _need_mail(ctx: Context) -> str | None:
    return None if ctx.mailer is not None else "E-Mail (Google Workspace) nicht eingerichtet"


JOBS: tuple[Job, ...] = (
    Job("billing", "Vertragsabrechnung", "Erstellt fällige Rechnungen aus aktiven Verträgen; ab der zweiten Rechnung wird automatisch "
        "ausgestellt und versendet.", "daily@6", "täglich 06:00", _billing),
    Job("dunning", "Mahnlauf", "Erstellt fällige Zahlungserinnerungen und Mahnungen (3 Stufen, Zinsen, Gebühren) und versendet sie "
        "nach Einstellung.", "daily@7", "täglich 07:00", _dunning),
    Job("receipt_inbox", "Belegpostfach", "Holt Rechnungen aus dem Postfach, erkennt E-Rechnungen sofort und andere Belege per KI und "
        "legt Belegentwürfe zur Prüfung an.", "every@15", "alle 15 Minuten", _receipt_inbox, _need_mail),
    Job("auto_match", "Zahlungszuordnung", "Ordnet importierte Umsätze offenen Rechnungen zu, sobald Rechnungsnummer und Betrag "
        "eindeutig passen.", "every@60", "stündlich", _auto_match),
    Job("digest", "Wochenüberblick per E-Mail", "Montags eine Zusammenfassung mit offenen Aufgaben, Forderungen und MRR an alle "
        "Gesellschafter.", "weekly@0@8", "montags 08:00", _digest, _need_mail),
    Job("postings", "Buchungsjournal", "Bucht Ausgangsrechnungen, Stornos und Zahlungen nach festen Regeln doppelt (Soll/Haben) ins "
        "Journal. Eingangsbelege kontiert die KI nur auf Klick.", "every@15", "alle 15 Minuten", _postings),
    Job("ledger_verify", "Integritätsprüfung (GoBD)", "Prüft täglich die komplette Hash-Kette aller Festschreibungen. Bei einer "
        "Abweichung: Alarm per E-Mail und rote Aufgabe in der Übersicht.", "daily@2", "täglich 02:00", _ledger_verify),
    Job("liquidity", "Liquiditätswächter", "Prüft täglich, ob das frei verfügbare Kapital (Kontostand minus USt, Ertragsteuern, "
        "Verbindlichkeiten, Gehälter) unter die Mindestreserve fällt, und warnt die Geschäftsführung per E-Mail.", "daily@8",
        "täglich 08:00", _liquidity_alert),
    Job("monthly_report", "Monatsbericht an die Geschäftsführung", "Am 3. jedes Monats: Ergebnis des Vormonats, USt-Zahllast, "
        "Liquidität und offene Punkte für den Monatsabschluss per E-Mail.", "monthly@3@8", "monatlich am 3., 08:00", _monthly_report,
        _need_mail),
    Job("offer_reminder", "Angebots-Erinnerung an Kunden", "Erinnert Kunden einmalig an offene Angebote nach der eingestellten Frist "
        "(innerhalb der Bindefrist, mit Portal-Link). Standardmäßig aus – bewusst einschalten.", "daily@9", "täglich 09:00",
        _offer_reminder, _need_mail, default_enabled=False),
    Job("archive", "Dokumentenarchiv", "Übernimmt Rechnungen, gebuchte Belege, Mahnungen, signierte Verträge, Angebote, "
        "Gehaltsabrechnungen und SEPA-Dateien unveränderlich ins Archiv (mit Hash-Kette und Aufbewahrungsfrist).", "every@60",
        "stündlich", _archive),
    Job("archive_replication", "Archiv prüfen und spiegeln (GoBD)", "Prüft täglich Hash-Kette und Prüfsummen aller Archivdokumente, "
        "kopiert neue Dokumente auf die eingerichteten Ziele (S3 mit Object Lock, WebDAV, Festplatte) und prüft eine Stichprobe "
        "der Kopien.",
        "daily@2", "täglich 02:00", _archive_replication),
    Job("documenso_sync", "Documenso-Abgleich", "Übernimmt Verträge, die direkt in Documenso angelegt oder bearbeitet wurden, gleicht den "
        "Signaturstatus ab und archiviert abgeschlossene Verträge (ergänzt die sofortige Meldung per Webhook).", "every@15",
        "alle 15 Minuten", _documenso_sync),
    Job("payroll_reports", "SV-Meldungen vorbereiten", "Legt fällige DEÜV-Meldungen (An-, Ab-, Jahres-, Unterbrechungsmeldung) mit Frist "
        "an – Grundlage für „Zu erledigen“.", "every@60", "stündlich", _payroll_reports),
    Job("webhooks", "Webhooks zustellen", "Meldet fachliche Ereignisse (z. B. Rechnung ausgestellt, Beleg gebucht) an die "
        "eingerichteten Webhook-Ziele – mit Signatur und Wiederholungen.", "every@1", "jede Minute", _webhooks),
    Job("workflows", "Eigene Workflows", "Führt eure Workflows aus (Zeitplan oder Ereignis) – Einrichtung im Tab „Eigene Workflows“.",
        "every@1", "jede Minute", _workflows),
    Job("exchange_rates", "EZB-Wechselkurse", "Holt werktags die Referenzkurse der Europäischen Zentralbank für Rechnungen in "
        "Fremdwährung (nur wenn Kunden in Fremdwährung abgerechnet werden).", "daily@17", "täglich 17:00", _exchange_rates),
    Job("cleanup", "Datenbereinigung", "Löscht abgelaufene Portal-Anmeldecodes, -sitzungen und alte Protokolle (Datensparsamkeit).",
        "daily@3", "täglich 03:00", _cleanup),
)
BY_KEY = {j.key: j for j in JOBS}


# ───────────────────────── Steuerung ─────────────────────────

def is_due(schedule: str, now: dt.datetime, last: dt.datetime | None) -> bool:
    """``now`` in Berlin-Zeit; ``last`` = Start des letzten Laufs (beliebige Zeitzone)."""
    last_local = last.astimezone(BERLIN) if last else None
    kind, *args = schedule.split("@")
    if kind == "daily":
        return now.hour >= int(args[0]) and (last_local is None or last_local.date() != now.date())
    if kind == "every":
        return last is None or (now - last_local) >= dt.timedelta(minutes=int(args[0])) if last_local else True
    if kind == "monthly":
        return now.day >= int(args[0]) and now.hour >= int(args[1]) and (
            last_local is None or (last_local.year, last_local.month) != (now.year, now.month))
    if kind == "weekly":
        return now.weekday() == int(args[0]) and now.hour >= int(args[1]) and (last_local is None or last_local.date() != now.date())
    raise ValueError(schedule)


def _lock_key(key: str, tenant_id: UUID | None = None) -> int:
    return int.from_bytes(hashlib.sha256(f"automation:{tenant_id}:{key}".encode()).digest()[:7], "big")


async def enabled_map(conn: AsyncConnection, tenant_id: UUID) -> dict[str, bool]:
    rows = {r.key: r.enabled for r in await conn.execute(text("SELECT key, enabled FROM automation_settings WHERE tenant_id = :t"),
                                                          {"t": tenant_id})}
    return {j.key: rows.get(j.key, j.default_enabled) for j in JOBS}


async def run_job(ctx: Context, job: Job, trigger: str, today: dt.date) -> dict[str, Any]:
    from app.core.tenancy import set_current

    set_current(ctx.tenant_id)  # z. B. für die Archivierung versendeter Mails dieser Firma
    reason = job.available(ctx)
    async with ctx.engine.connect() as lock_conn:
        if not await lock_conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(job.key, ctx.tenant_id)}):
            return {"status": "skipped", "summary": "läuft bereits"}
        try:
            async with ctx.engine.begin() as conn:
                run_id = await conn.scalar(text(
                    "INSERT INTO automation_runs (tenant_id, key, trigger, actor) VALUES (:t, :k, :tr, :a) RETURNING id"),
                    {"t": ctx.tenant_id, "k": job.key, "tr": trigger, "a": ctx.actor})
            details: dict[str, Any] = {}
            if reason:
                status, summary = "skipped", reason
            else:
                try:
                    status, summary, details = await job.run(ctx, today)
                except Exception as exc:
                    log.exception("Automatisierung %s fehlgeschlagen", job.key)
                    status, summary, details = "error", f"{type(exc).__name__}: {exc}"[:500], {}
            async with ctx.engine.begin() as conn:
                await conn.execute(text(
                    "UPDATE automation_runs SET finished_at = now(), status = :s, summary = :sum, details = CAST(:d AS jsonb) "
                "WHERE id = :id"),
                    {"s": status, "sum": summary[:1000], "d": json.dumps(details, default=str)[:200_000], "id": run_id})
            return {"status": status, "summary": summary}
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(job.key, ctx.tenant_id)})


async def overview(conn: AsyncConnection, ctx: Context) -> list[dict[str, Any]]:
    enabled = await enabled_map(conn, ctx.tenant_id)
    out = []
    for j in JOBS:
        last = (await conn.execute(text(
            "SELECT started_at, finished_at, status, summary, trigger FROM automation_runs WHERE tenant_id = :t AND key = :k "
            "ORDER BY started_at DESC LIMIT 1"), {"t": ctx.tenant_id, "k": j.key})).mappings().one_or_none()
        recent = (await conn.execute(text(
            "SELECT started_at, status, summary, trigger FROM automation_runs WHERE tenant_id = :t AND key = :k "
            "ORDER BY started_at DESC LIMIT 5"), {"t": ctx.tenant_id, "k": j.key})).mappings().all()
        out.append({"key": j.key, "label": j.label, "description": j.description, "schedule": j.schedule_label,
                    "enabled": enabled[j.key], "unavailable": j.available(ctx), "last": dict(last) if last else None,
                    "recent": [dict(r) for r in recent]})
    return out


async def set_enabled(conn: AsyncConnection, tenant_id: UUID, key: str, enabled: bool, actor: str) -> None:
    if key not in BY_KEY:
        raise KeyError(key)
    await conn.execute(text(
        "INSERT INTO automation_settings (tenant_id, key, enabled, updated_by) VALUES (:t, :k, :e, :a) "
        "ON CONFLICT (tenant_id, key) DO UPDATE SET enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = now()"),
        {"t": tenant_id, "k": key, "e": enabled, "a": actor})
    from app.services import audit

    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="automation.toggled", entity_type="automation", entity_id=None,
                       after={"key": key, "enabled": enabled})


async def scheduler(ctx_factory: Callable[[], Context], interval: float = 60.0) -> None:
    """Endlosschleife: prüft jede Minute, welche aktivierten Jobs fällig sind."""
    first = True
    import dataclasses

    from app.services.companies import all_ids

    while True:
        try:
            base = ctx_factory()
            async with base.engine.begin() as conn:
                if first:  # nach Neustart hängengebliebene Läufe abschließen
                    await conn.execute(text("UPDATE automation_runs SET status = 'error', finished_at = now(), "
                                            "summary = 'abgebrochen (Neustart)' "
                                            "WHERE status = 'running' AND started_at < now() - interval '1 hour'"))
                    first = False
                tenants = await all_ids(conn, base.tenant_id)
            for tid in tenants:  # jede Firma hat eigene Jobs, Einstellungen und Protokolle
                ctx = dataclasses.replace(base, tenant_id=tid)
                async with ctx.engine.begin() as conn:
                    enabled = await enabled_map(conn, ctx.tenant_id)
                    last = {r.key: r.started for r in await conn.execute(text(
                        "SELECT key, MAX(started_at) AS started FROM automation_runs WHERE tenant_id = :t AND trigger = 'schedule' "
                        "GROUP BY key"),
                        {"t": ctx.tenant_id})}
                now = dt.datetime.now(BERLIN)
                for job in JOBS:
                    # Nicht eingerichtete Jobs (z. B. ohne Postfach) nicht im Viertelstundentakt protokollieren
                    if enabled[job.key] and job.available(ctx) is None and is_due(job.schedule, now, last.get(job.key)):
                        await run_job(ctx, job, "schedule", now.date())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduler-Durchlauf fehlgeschlagen")
        await asyncio.sleep(interval)


# ───────────────────────── Aufgaben & Kennzahlen ─────────────────────────

async def _business_tasks(conn: AsyncConnection, tenant_id: UUID, today: dt.date, ledger: Any, add: Any, count: Any) -> None:
    """Prüfungen aus Sicht von Controlling, Steuerberatung und Geschäftsführung."""
    from app.services import finance

    def eur(v: Decimal) -> str:
        return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

    # Liquidität: verfügbares Kapital unter Mindestreserve
    try:
        cap = await finance.capital(conn, tenant_id, today, ledger)
        if cap["below_minimum"]:
            add("red", "Liquidität unter Mindestreserve", "/", f"verfügbar {eur(cap['available'])} (Reserve {eur(cap['min_liquidity'])})")
        if cap["balance"]["estimated"]:
            add("blue", "Kontostand erfassen", "/buchhaltung", "für eine exakte Liquiditätsübersicht (oder CAMT-Auszug importieren)")
    except Exception:
        log.warning("Liquidität für Aufgaben nicht berechenbar", exc_info=True)
    # GoBD Belegprinzip: Ausgaben auf dem Konto ohne Beleg
    n = await count("SELECT count(*) FROM bank_transactions WHERE tenant_id = :t AND status = 'open' AND amount < 0 AND booked_on < :d",
                    d=today - dt.timedelta(days=7))
    if n:
        add("amber", f"{n} Kontoabbuchung(en) ohne Beleg seit über 7 Tagen", "/buchhaltung", "Belegprinzip (GoBD): Beleg beschaffen")
    # Zeitgerechte Erfassung
    n = await count("SELECT count(*) FROM receipts WHERE tenant_id = :t AND status = 'draft' AND created_at < :d",
                    d=today - dt.timedelta(days=10))
    if n:
        add("amber", f"{n} Belegentwurf/-entwürfe seit über 10 Tagen nicht gebucht", "/belege", "zeitgerechte Erfassung (GoBD)")
    # Wiederkehrende Lieferanten: Beleg des laufenden Monats fehlt
    if today.day >= 10:
        rows = await conn.execute(text(
            "SELECT s.name FROM suppliers s WHERE s.tenant_id = :t AND s.archived_at IS NULL "
            "AND (SELECT count(DISTINCT to_char(r.receipt_date, 'YYYY-MM')) FROM receipts r WHERE r.supplier_id = s.id "
            "AND r.kind = 'expense' AND r.receipt_date >= :s2 AND r.receipt_date < :m) = 2 "
            "AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.supplier_id = s.id AND r.receipt_date >= :m)"),
            {"t": tenant_id, "m": today.replace(day=1), "s2": (today.replace(day=1) - dt.timedelta(days=45)).replace(day=1)})
        for (name,) in rows:
            add("blue", f"Monatsbeleg von {name} für {today:%m/%Y} fehlt", "/belege", "regelmäßiger Lieferant – Rechnung anfordern")
    # Angebote nachfassen
    fs = await finance.settings(conn, tenant_id)
    n = await count("SELECT count(*) FROM offers WHERE tenant_id = :t AND status = 'sent' AND sent_at < :d AND valid_until >= :today",
                    d=today - dt.timedelta(days=int(fs["offer_followup_days"])), today=today)
    if n:
        add("blue", f"{n} Angebot(e) ohne Rückmeldung seit {fs['offer_followup_days']} Tagen", "/angebote", "nachfassen")
    # Verträge: Laufzeitende ohne Verlängerung bzw. gekündigte Verträge
    n = await count("SELECT count(*) FROM contracts WHERE tenant_id = :t AND status = 'active' AND end_date BETWEEN :today AND :d",
                    today=today, d=today + dt.timedelta(days=45))
    if n:
        add("amber", f"{n} Vertrag/Verträge enden in den nächsten 45 Tagen", "/vertraege",
            "Verlängerung anbieten oder Abschluss vorbereiten")
    # Lohn: Geschäftsführer mit Gehalt, aber ohne Steuerdaten
    n = await count("SELECT count(*) FROM shareholders s WHERE s.tenant_id = :t AND s.is_managing_director AND EXISTS "
                    "(SELECT 1 FROM md_compensations c WHERE c.shareholder_id = s.id) AND NOT EXISTS "
                    "(SELECT 1 FROM md_tax_profiles p WHERE p.shareholder_id = s.id)")
    if n:
        add("amber", f"Steuerdaten für {n} Geschäftsführer fehlen", "/gehaelter",
            "Steuerklasse, Kasse usw. – sonst ist die Gehaltsrechnung ungenau")
    # Budgets: Bereiche über Monatsbudget
    try:
        from app.services import budgets

        for area in (await budgets.overview(conn, tenant_id, today.year, today))["areas"]:
            if area["over_budget"]:
                add("amber", f"Budget „{area['name']}“ überschritten", "/budgets",
                    f"{eur(area['month_spent'])} von {eur(Decimal(area['monthly_budget']))} im {today:%m/%Y}")
    except Exception:
        log.warning("Budgets für Aufgaben nicht verfügbar", exc_info=True)
    # Jahresabschluss: Januar bis Mai Hinweis für das Vorjahr
    if today.month <= 5:
        add("blue", f"Jahresabschluss {today.year - 1} mit dem Steuerberater vorbereiten", "/buchhaltung",
            "UG-Rücklage 25 % (§ 5a Abs. 3 GmbHG), Feststellung durch Gesellschafterbeschluss, Offenlegung bis 31.12.")


async def kpis(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> dict[str, Any]:
    from app.services import receivables as rv
    from app.services.contracts import list_contracts

    items = await rv.open_items(conn, tenant_id, today)
    mrr = sum((c["mrr"] for c in await list_contracts(conn, tenant_id)), start=D("0.00"))
    month_start = today.replace(day=1)
    revenue_month = D(await conn.scalar(text(
        "SELECT COALESCE(SUM(total_net), 0) FROM invoices WHERE tenant_id = :t AND status = 'issued' AND issue_date >= :s"),
        {"t": tenant_id, "s": month_start}))
    expenses_month = D(await conn.scalar(text(
        "SELECT COALESCE(SUM(expense_amount), 0) FROM receipts WHERE tenant_id = :t AND status = 'booked' AND receipt_date >= :s"),
        {"t": tenant_id, "s": month_start}))
    return {"open_receivables": items["total_open"], "overdue": items["overdue"], "open_count": len(items["items"]), "mrr": mrr,
            "revenue_month": revenue_month, "expenses_month": expenses_month}


async def _personnel_tasks(conn: AsyncConnection, tenant_id: UUID, today: dt.date, add: Any, count: Any) -> None:
    """Personal: fällige Checklistenpunkte, Urlaubsanträge, Löschfristen der Personalakte."""
    overdue = await count("SELECT count(*) FROM hr_checklist_items WHERE tenant_id = :t AND done_at IS NULL AND due_date < :d", d=today)
    soon = await count("SELECT count(*) FROM hr_checklist_items WHERE tenant_id = :t AND done_at IS NULL AND due_date BETWEEN :d AND :d7",
                       d=today, d7=today + dt.timedelta(days=7))
    if overdue:
        add("red", f"{overdue} Personal-Aufgabe(n) überfällig (On-/Offboarding)", "/mitarbeiter?tab=personal",
            "z. B. SV-Anmeldung, Unterweisung, Zeugnis", overdue)
    if soon:
        add("amber", f"{soon} Personal-Aufgabe(n) in den nächsten 7 Tagen", "/mitarbeiter?tab=personal", None, soon)
    n = await count("SELECT count(*) FROM absences WHERE tenant_id = :t AND status = 'requested'")
    if n:
        add("amber", f"{n} Urlaubsantrag/-anträge entscheiden", "/abwesenheit/team", None, n)
    n = await count("SELECT count(*) FROM personnel_documents WHERE tenant_id = :t AND deleted_at IS NULL AND delete_after < :d", d=today)
    if n:
        add("amber", f"{n} Dokument(e) der Personalakte nach Löschfrist löschen", "/mitarbeiter?tab=personal", "Art. 17 DSGVO", n)
    late = await count("SELECT count(*) FROM sv_reports WHERE tenant_id = :t AND status = 'open' AND due_date < :d", d=today)
    soon = await count("SELECT count(*) FROM sv_reports WHERE tenant_id = :t AND status = 'open' AND due_date BETWEEN :d AND :d14",
                       d=today, d14=today + dt.timedelta(days=14))
    if late:
        add("red", f"{late} SV-Meldung(en) überfällig (DEÜV)", "/mitarbeiter?tab=lohn", "im SV-Meldeportal übermitteln", late)
    if soon:
        add("amber", f"{soon} SV-Meldung(en) in den nächsten 14 Tagen fällig", "/mitarbeiter?tab=lohn", None, soon)
    n = await count("SELECT count(*) FROM contribution_statements WHERE tenant_id = :t AND status = 'open' AND submit_by <= :d7",
                    d7=today + dt.timedelta(days=7))
    if n:
        add("red", f"{n} Beitragsnachweis(e) an die Krankenkassen übermitteln", "/mitarbeiter?tab=lohn",
            "spätestens am fünftletzten Bankarbeitstag", n)
    if today.month in (1, 2):
        n = await count("SELECT count(*) FROM wage_tax_certificates WHERE tenant_id = :t AND year = :y AND status = 'draft'",
                        y=today.year - 1)
        if n:
            add("amber", f"{n} Lohnsteuerbescheinigung(en) {today.year - 1} bis 28.02. übermitteln", "/mitarbeiter?tab=lohn", None, n)


async def open_tasks(conn: AsyncConnection, tenant_id: UUID, today: dt.date, ledger: Any = None) -> list[dict[str, Any]]:
    """Alles, was gerade Aufmerksamkeit braucht – berechnet, nicht gespeichert (immer aktuell)."""
    from app.services import receivables as rv

    tasks: list[dict[str, Any]] = []

    def add(severity: str, title: str, href: str, detail: str | None = None, count: int | None = None) -> None:
        tasks.append({"severity": severity, "title": title, "href": href, "detail": detail, "count": count})

    async def count(sql: str, **params: Any) -> int:
        return int(await conn.scalar(text(sql), {"t": tenant_id, **params}) or 0)

    broken = (await conn.execute(text("SELECT status FROM automation_runs WHERE tenant_id = :t AND key = 'ledger_verify' "
                                      "AND status IN ('ok', 'error') ORDER BY started_at DESC LIMIT 1"), {"t": tenant_id})).scalar()
    if broken == "error":
        add("red", "Integritätsprüfung: Hash-Kette der Festschreibungen gebrochen", "/automatisierungen",
            "Sofort Steuerberater informieren, Sicherung aufbewahren")
    arch = (await conn.execute(text("SELECT status, summary FROM automation_runs WHERE tenant_id = :t AND key = 'archive_replication' "
                                    "AND status IN ('ok', 'error') ORDER BY started_at DESC LIMIT 1"), {"t": tenant_id})).first()
    if arch and arch[0] == "error":
        add("red", "Archiv: Prüfung oder Spiegelung fehlgeschlagen", "/archiv", arch[1])
    n = await count("SELECT count(*) FROM contract_terminations WHERE tenant_id = :t AND status = 'received'")
    if n:
        add("red", f"{n} Kündigung(en)/Widerruf(e) bearbeiten", "/vertraege", "Eingang ist bereits wirksam", n)
    items = await rv.open_items(conn, tenant_id, today)
    if items["rate_missing"]:
        add("red", f"Basiszinssatz ab {items['rate_missing']:%d.%m.%Y} eintragen", "/forderungen", "sonst keine Mahnungen mit Zinsen")
    overdue = [i for i in items["items"] if i["status"] == "overdue" and not i["blocked"]]
    if overdue:
        total = sum((i["total_open"] for i in overdue), start=D(0))
        add("amber", f"{len(overdue)} überfällige Rechnung(en)", "/forderungen", f"{total:.2f} € offen".replace(".", ","), len(overdue))
    n = await count("SELECT count(*) FROM dunning_notices WHERE tenant_id = :t AND sent_at IS NULL")
    if n:
        add("amber", f"{n} Mahnschreiben noch nicht versendet", "/forderungen", None, n)
    n = await count("SELECT count(*) FROM invoices WHERE tenant_id = :t AND status = 'draft'")
    if n:
        add("blue", f"{n} Rechnungsentwurf/-entwürfe prüfen und ausstellen", "/rechnungen", None, n)
    n = await count("SELECT count(*) FROM receipts WHERE tenant_id = :t AND status = 'draft'")
    if n:
        add("blue", f"{n} Belegentwurf/-entwürfe prüfen und buchen", "/belege", None, n)
    n = await count("SELECT count(*) FROM receipt_files f WHERE f.tenant_id = :t "
                    "AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.file_id = f.id)")
    if n:
        add("blue", f"{n} Belegdatei(en) ohne Beleg", "/belege", "aus Upload oder Belegpostfach", n)
    n = await count("SELECT count(*) FROM bank_transactions WHERE tenant_id = :t AND status = 'open'")
    if n:
        add("blue", f"{n} Bankumsatz/-umsätze zuordnen", "/buchhaltung", None, n)
    expiring = await count("SELECT count(*) FROM offers WHERE tenant_id = :t AND status = 'sent' AND valid_until BETWEEN :d AND :d3",
                           d=today, d3=today + dt.timedelta(days=3))
    if expiring:
        add("amber", f"{expiring} Angebot(e) laufen in ≤ 3 Tagen ab", "/angebote", "nachfassen", expiring)
    n = await count("SELECT count(*) FROM offers WHERE tenant_id = :t AND status = 'accepted' "
                    "AND invoice_id IS NULL AND contract_id IS NULL")
    if n:
        add("blue", f"{n} angenommene(s) Angebot(e) in Rechnung/Vertrag übernehmen", "/angebote", None, n)
    n = await count("SELECT count(*) FROM signature_requests WHERE tenant_id = :t AND status = 'pending' "
                    "AND created_at < now() - interval '7 days'")
    if n:
        add("amber", f"{n} Signatur(en) seit über 7 Tagen offen", "/angebote", "Erinnerung senden oder Status abgleichen", n)
    if today.day >= 10:
        prev_end = today.replace(day=1) - dt.timedelta(days=1)
        exported = await count("SELECT count(*) FROM datev_exports WHERE tenant_id = :t AND kind IN ('final','correction') "
                               "AND period_from <= :s AND period_to >= :e", s=prev_end.replace(day=1), e=prev_end)
        configured = await count("SELECT count(*) FROM datev_settings WHERE tenant_id = :t AND consultant_number IS NOT NULL")
        if configured and not exported:
            add("amber", f"DATEV-Export {prev_end:%m/%Y} an den Steuerberater", "/buchhaltung")
    try:
        from app.services import tax_report

        settings = get_settings()
        locked = set(await ledger.locked_periods(tenant_id=tenant_id)) if ledger is not None else set()
        active = await tax_report.active_months(conn, tenant_id)
        for d in tax_report.calendar(today.year, filing=settings.vat_filing, extension=settings.vat_extension, locked=locked,
                                     active=active, today=today):
            relevant = d["kind"] != "ustva" or d["active"] or d["overdue"]
            if d["overdue"]:
                add("red", f"Überfällig: {d['label']}", "/steuern", f"war fällig am {d['due']:%d.%m.%Y}")
            elif relevant and not d["done"] and today <= d["due"] <= today + dt.timedelta(days=14):
                add("red" if (d["due"] - today).days <= 3 else "amber", f"Frist: {d['label']}", "/steuern",
                    f"fällig am {d['due']:%d.%m.%Y}")
    except Exception:
        log.warning("Steuerfristen für die Aufgabenliste nicht verfügbar", exc_info=True)
    await _business_tasks(conn, tenant_id, today, ledger, add, count)
    await _personnel_tasks(conn, tenant_id, today, add, count)
    order = {"red": 0, "amber": 1, "blue": 2}
    return sorted(tasks, key=lambda t: order[t["severity"]])
