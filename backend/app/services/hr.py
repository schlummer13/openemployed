"""Personalverwaltung: On-/Offboarding-Checklisten, Vertragsvorlagen und digitale Personalakte.

- Checklisten entstehen automatisch beim Anlegen (Onboarding) bzw. beim ersten Eintragen eines Austrittsdatums (Offboarding)
  aus der Standardvorlage; die Vorlagen enthalten die gesetzlich relevanten Schritte mit Fristen (NachwG, DEÜV, ArbSchG, BUrlG …).
- Personalakte: Dokumente je Mitarbeiter (nicht im GoBD-Archiv, weil Personalakten nach Ende der Aufbewahrung gelöscht werden
  müssen – Art. 17 DSGVO). Standard-Löschfrist: 3 Jahre nach Jahresende des Austritts (Verjährung § 195 BGB);
  Lohnunterlagen liegen zusätzlich revisionssicher im Archiv (6/8 Jahre).
- Verträge aus Vorlagen mit Platzhaltern → PDF → optional digitale Signatur über Documenso (Mitarbeiter + Geschäftsführung).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html as html_lib
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

DOC_CATEGORIES = {
    "contract": "Arbeitsvertrag", "amendment": "Vertragsänderung", "application": "Bewerbung", "certificate": "Nachweis/Zertifikat",
    "reference": "Zeugnis", "warning": "Abmahnung", "identity": "Ausweis/Aufenthaltstitel", "training": "Schulung/Unterweisung",
    "health": "Gesundheit (nur Pflichtangaben)", "payroll": "Lohnunterlage", "termination": "Kündigung/Aufhebung", "other": "Sonstiges",
}
ALLOWED_MIME = {"application/pdf", "image/png", "image/jpeg", "text/plain",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_BYTES = 20 * 1024 * 1024

ONBOARDING: list[dict[str, Any]] = [
    {"title": "Arbeitsvertrag unterschrieben", "offset": -14, "responsible": "Geschäftsführung",
     "legal": "Nachweisgesetz § 2: Name, Entgelt, Arbeitszeit spätestens am 1. Arbeitstag schriftlich"},
    {"title": "Steuer-ID und Geburtsdatum erfasst (ELStAM-Anmeldung)", "offset": 0, "responsible": "Personal", "legal": "§ 39e EStG"},
    {"title": "Sozialversicherungsnummer und Krankenkasse erfasst", "offset": 0, "responsible": "Personal"},
    {"title": "Anmeldung zur Sozialversicherung (DEÜV, Grund 10)", "offset": 42, "responsible": "Personal",
     "legal": "§ 6 DEÜV: mit der ersten Entgeltabrechnung, spätestens 6 Wochen nach Beginn; Minijob an die Minijob-Zentrale"},
    {"title": "Bankverbindung hinterlegt", "offset": 0, "responsible": "Personal"},
    {"title": "Verpflichtung auf Vertraulichkeit und Datenschutz", "offset": 0, "responsible": "Geschäftsführung",
     "legal": "Art. 29, 32 Abs. 4 DSGVO"},
    {"title": "Unterweisung Arbeitsschutz (Bildschirmarbeit, Brandschutz)", "offset": 0, "responsible": "Geschäftsführung",
     "legal": "§ 12 ArbSchG – vor Aufnahme der Tätigkeit, jährlich wiederholen"},
    {"title": "Aufenthaltstitel geprüft und Kopie abgelegt (nur Nicht-EU)", "offset": -1, "responsible": "Personal",
     "legal": "§ 4a Abs. 5 AufenthG"},
    {"title": "Arbeitsmittel bereitgestellt (Laptop, Zugänge, E-Mail)", "offset": -1, "responsible": "IT"},
    {"title": "Benutzerkonto OpenEmployed und Mitarbeiter-Portal (Stempeluhr, Urlaub)", "offset": 0, "responsible": "Geschäftsführung"},
    {"title": "Einarbeitungsplan und Ansprechperson festgelegt", "offset": 0, "responsible": "Teamleitung"},
    {"title": "Feedbackgespräch vor Ende der Probezeit", "offset": 160, "responsible": "Teamleitung",
     "legal": "Kündigung in der Probezeit mit 2 Wochen Frist (§ 622 Abs. 3 BGB)"},
]
OFFBOARDING: list[dict[str, Any]] = [
    {"title": "Kündigung/Aufhebungsvertrag im Original abgelegt", "offset": -60, "responsible": "Geschäftsführung",
     "legal": "Schriftform § 623 BGB – keine E-Mail, kein Fax; Zugang dokumentieren"},
    {"title": "Hinweis auf frühzeitige Arbeitsuchendmeldung gegeben", "offset": -60, "responsible": "Personal",
     "legal": "§ 2 Abs. 2 Nr. 3 SGB III (Meldung binnen 3 Tagen bzw. 3 Monate vorher)"},
    {"title": "Resturlaub gewährt oder Abgeltung im letzten Lohnlauf", "offset": -30, "responsible": "Personal",
     "legal": "§ 7 Abs. 4 BUrlG"},
    {"title": "Zeitkonto ausgeglichen oder ausgezahlt", "offset": -7, "responsible": "Personal"},
    {"title": "Arbeitszeugnis erstellt (auf Wunsch qualifiziert)", "offset": 0, "responsible": "Geschäftsführung", "legal": "§ 109 GewO"},
    {"title": "Urlaubsbescheinigung ausgehändigt", "offset": 0, "responsible": "Personal", "legal": "§ 6 Abs. 2 BUrlG"},
    {"title": "Arbeitsbescheinigung an die Bundesagentur (elektronisch)", "offset": 7, "responsible": "Personal",
     "legal": "§ 312 SGB III"},
    {"title": "Abmeldung Sozialversicherung (DEÜV, Grund 30)", "offset": 42, "responsible": "Personal",
     "legal": "§ 8 DEÜV: mit der nächsten Entgeltabrechnung, spätestens 6 Wochen nach Ende"},
    {"title": "ELStAM-Abmeldung und Lohnsteuerbescheinigung", "offset": 30, "responsible": "Personal", "legal": "§ 41b EStG"},
    {"title": "Arbeitsmittel zurückgegeben", "offset": 0, "responsible": "IT"},
    {"title": "Zugänge gesperrt (E-Mail, Systeme, OpenEmployed, Portal)", "offset": 1, "responsible": "IT"},
    {"title": "Personalakte: Löschfristen geprüft", "offset": 30, "responsible": "Personal",
     "legal": "Art. 17 DSGVO – nicht mehr erforderliche Unterlagen löschen"},
]

DEFAULT_CONTRACT = """# Arbeitsvertrag

zwischen **{{firma.name}}**, {{firma.anschrift}} (Arbeitgeber)
und **{{mitarbeiter.name}}**, geboren am {{mitarbeiter.geburtsdatum}}, {{mitarbeiter.anschrift}} (Arbeitnehmer/in).

## § 1 Beginn und Tätigkeit
Das Arbeitsverhältnis beginnt am **{{vertrag.beginn}}**{{vertrag.befristung}}. Die Arbeitnehmerin/der Arbeitnehmer wird als
**{{mitarbeiter.position}}** beschäftigt. Arbeitsort ist {{firma.ort}}; mobiles Arbeiten nach Absprache.

## § 2 Probezeit
Die ersten sechs Monate gelten als Probezeit. Während der Probezeit kann das Arbeitsverhältnis mit einer Frist von zwei Wochen
gekündigt werden.

## § 3 Arbeitszeit
Die regelmäßige wöchentliche Arbeitszeit beträgt **{{vertrag.wochenstunden}} Stunden** an den Tagen {{vertrag.arbeitstage}}.
Die Arbeitszeit wird über die Stempeluhr erfasst. Überstunden werden {{vertrag.ueberstunden}}.

## § 4 Vergütung
Die Vergütung beträgt **{{vertrag.verguetung}}** brutto. Sie wird jeweils am Monatsende abgerechnet und auf das benannte Konto
überwiesen. Zusammensetzung und Höhe weiterer Bestandteile ergeben sich aus der monatlichen Entgeltabrechnung.

## § 5 Urlaub
Der Urlaubsanspruch beträgt **{{vertrag.urlaubstage}} Arbeitstage** im Kalenderjahr bei {{vertrag.arbeitstage_anzahl}} Arbeitstagen
pro Woche. Der gesetzliche Mindesturlaub wird zuerst gewährt.

## § 6 Arbeitsverhinderung
Eine Arbeitsunfähigkeit ist unverzüglich mitzuteilen. Die Entgeltfortzahlung richtet sich nach dem Entgeltfortzahlungsgesetz.

## § 7 Kündigung
Nach der Probezeit gelten die gesetzlichen Kündigungsfristen (§ 622 BGB). Die Kündigung bedarf der Schriftform (§ 623 BGB).
Will die Arbeitnehmerin/der Arbeitnehmer geltend machen, dass eine Kündigung unwirksam ist, muss innerhalb von drei Wochen nach
Zugang Klage beim Arbeitsgericht erhoben werden (§ 4 KSchG).

## § 8 Verschwiegenheit, Datenschutz
Über Betriebs- und Geschäftsgeheimnisse ist auch nach Ende des Arbeitsverhältnisses Stillschweigen zu bewahren.

## § 9 Sonstiges
Tarifverträge und Betriebsvereinbarungen finden keine Anwendung. Eine betriebliche Altersversorgung wird auf Wunsch per
Entgeltumwandlung angeboten. Änderungen dieses Vertrags bedürfen der Textform.

{{firma.ort}}, den {{heute}}
"""


def _seed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"title": i["title"], "offset": i["offset"], "responsible": i.get("responsible"), "legal": i.get("legal"),
             "description": i.get("description")} for i in items]


async def ensure_templates(conn: AsyncConnection, tenant_id: UUID) -> None:
    import json

    # parallele Erstaufrufe (z. B. zwei Komponenten gleichzeitig) dürfen die Vorlagen nicht doppelt anlegen
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"hr-templates:{tenant_id}"})

    for kind, items, name in (("onboarding", ONBOARDING, "Standard-Onboarding"), ("offboarding", OFFBOARDING, "Standard-Offboarding")):
        if not await conn.scalar(text("SELECT 1 FROM hr_checklist_templates WHERE tenant_id = :t AND kind = :k"),
                                 {"t": tenant_id, "k": kind}):
            await conn.execute(text("INSERT INTO hr_checklist_templates (tenant_id, kind, name, items, is_default) "
                                    "VALUES (:t, :k, :n, CAST(:i AS jsonb), true)"),
                               {"t": tenant_id, "k": kind, "n": name, "i": json.dumps(_seed_items(items))})
    if not await conn.scalar(text("SELECT 1 FROM hr_contract_templates WHERE tenant_id = :t"), {"t": tenant_id}):
        await conn.execute(text("INSERT INTO hr_contract_templates (tenant_id, name, body, created_by) VALUES (:t, :n, :b, 'system')"),
                           {"t": tenant_id, "n": "Arbeitsvertrag (unbefristet, Muster)", "b": DEFAULT_CONTRACT})


# ───────────────────────── Checklisten ─────────────────────────

async def templates(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    await ensure_templates(conn, tenant_id)
    rows = await conn.execute(text("SELECT id, kind, name, items, is_default FROM hr_checklist_templates WHERE tenant_id = :t "
                                   "ORDER BY kind, is_default DESC, name"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def save_template(conn: AsyncConnection, tenant_id: UUID, template_id: UUID | None, data: dict[str, Any]) -> UUID:
    import json

    items = json.dumps(data["items"])
    if template_id is None:
        tid: UUID = await conn.scalar(text("INSERT INTO hr_checklist_templates (tenant_id, kind, name, items) VALUES "
                                           "(:t, :k, :n, CAST(:i AS jsonb)) RETURNING id"),
                                      {"t": tenant_id, "k": data["kind"], "n": data["name"], "i": items})
        return tid
    res = await conn.execute(text("UPDATE hr_checklist_templates SET name = :n, items = CAST(:i AS jsonb) "
                                  "WHERE tenant_id = :t AND id = :id"),
                             {"t": tenant_id, "id": template_id, "n": data["name"], "i": items})
    if not res.rowcount:
        raise NotFoundError("Vorlage nicht gefunden")
    return template_id


async def start_checklist(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, kind: str, reference: dt.date, actor: str,
                          template_id: UUID | None = None) -> UUID:
    await ensure_templates(conn, tenant_id)
    tpl = (await conn.execute(text(
        "SELECT name, items FROM hr_checklist_templates WHERE tenant_id = :t AND kind = :k AND (CAST(:id AS uuid) IS NULL OR id = :id) "
        "ORDER BY is_default DESC LIMIT 1"), {"t": tenant_id, "k": kind, "id": template_id})).mappings().one_or_none()
    if tpl is None:
        raise NotFoundError("Vorlage nicht gefunden")
    cid: UUID = await conn.scalar(text(
        "INSERT INTO hr_checklists (tenant_id, employee_id, kind, name, reference_date, created_by) VALUES (:t, :e, :k, :n, :d, :a) "
        "RETURNING id"), {"t": tenant_id, "e": employee_id, "k": kind, "n": tpl["name"], "d": reference, "a": actor})
    for pos, item in enumerate(tpl["items"]):
        due = reference + dt.timedelta(days=int(item.get("offset") or 0)) if item.get("offset") is not None else None
        await conn.execute(text(
            "INSERT INTO hr_checklist_items (tenant_id, checklist_id, position, title, description, responsible, due_date, legal) "
            "VALUES (:t, :c, :p, :ti, :de, :r, :d, :l)"),
            {"t": tenant_id, "c": cid, "p": pos, "ti": item["title"], "de": item.get("description"), "r": item.get("responsible"),
             "d": due, "l": item.get("legal")})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"hr.{kind}_started", entity_type="employee", entity_id=employee_id)
    return cid


async def on_employee_saved(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, *, created: bool, entry: dt.date,
                            exit_: dt.date | None, old_exit: dt.date | None, actor: str) -> None:
    """Startet Onboarding beim Anlegen und Offboarding beim ersten Austrittsdatum."""
    if created:
        await start_checklist(conn, tenant_id, employee_id, "onboarding", entry, actor)
    if exit_ and not old_exit and not await conn.scalar(text(
            "SELECT 1 FROM hr_checklists WHERE employee_id = :e AND kind = 'offboarding' AND completed_at IS NULL"), {"e": employee_id}):
        await start_checklist(conn, tenant_id, employee_id, "offboarding", exit_, actor)
    if exit_:
        await conn.execute(text(
            "UPDATE personnel_documents SET delete_after = :d WHERE employee_id = :e AND deleted_at IS NULL AND delete_after IS NULL "
            "AND category NOT IN ('payroll')"), {"e": employee_id, "d": dt.date(exit_.year + 3, 12, 31)})


async def checklists(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID | None = None, *,
                     open_only: bool = False) -> list[dict[str, Any]]:
    lists = [dict(r) for r in (await conn.execute(text(
        "SELECT c.id, c.employee_id, e.first_name || ' ' || e.last_name AS employee, c.kind, c.name, c.reference_date, c.created_at, "
        "c.completed_at FROM hr_checklists c JOIN employees e ON e.id = c.employee_id WHERE c.tenant_id = :t "
        "AND (CAST(:e AS uuid) IS NULL OR c.employee_id = :e) AND (NOT :o OR c.completed_at IS NULL) ORDER BY c.created_at DESC"),
        {"t": tenant_id, "e": employee_id, "o": open_only})).mappings()]
    if not lists:
        return []
    items = (await conn.execute(text(
        "SELECT id, checklist_id, position, title, description, responsible, due_date, legal, done_at, done_by FROM hr_checklist_items "
        "WHERE checklist_id = ANY(:ids) ORDER BY position"), {"ids": [c["id"] for c in lists]})).mappings().all()
    for c in lists:
        c["items"] = [dict(i) for i in items if i["checklist_id"] == c["id"]]
        c["done"] = sum(1 for i in c["items"] if i["done_at"])
        c["total"] = len(c["items"])
    return lists


async def toggle_item(conn: AsyncConnection, tenant_id: UUID, item_id: UUID, done: bool, actor: str) -> None:
    row = (await conn.execute(text("UPDATE hr_checklist_items SET done_at = CASE WHEN :d THEN now() END, "
                                   "done_by = CASE WHEN :d THEN :a END "
                                   "WHERE tenant_id = :t AND id = :id RETURNING checklist_id"),
                              {"t": tenant_id, "id": item_id, "d": done, "a": actor})).one_or_none()
    if row is None:
        raise NotFoundError("Punkt nicht gefunden")
    open_left = await conn.scalar(text("SELECT count(*) FROM hr_checklist_items WHERE checklist_id = :c AND done_at IS NULL"),
                                  {"c": row[0]})
    await conn.execute(text("UPDATE hr_checklists SET completed_at = CASE WHEN :o = 0 THEN coalesce(completed_at, now()) END "
                            "WHERE id = :c"),
                       {"o": open_left, "c": row[0]})


async def add_item(conn: AsyncConnection, tenant_id: UUID, checklist_id: UUID, data: dict[str, Any]) -> None:
    pos = await conn.scalar(text("SELECT coalesce(max(position), -1) + 1 FROM hr_checklist_items "
                                 "WHERE checklist_id = :c AND tenant_id = :t"),
                            {"c": checklist_id, "t": tenant_id})
    await conn.execute(text("INSERT INTO hr_checklist_items (tenant_id, checklist_id, position, title, responsible, due_date) "
                            "VALUES (:t, :c, :p, :ti, :r, :d)"),
                       {"t": tenant_id, "c": checklist_id, "p": pos, "ti": data["title"], "r": data.get("responsible"),
                        "d": data.get("due_date")})
    await conn.execute(text("UPDATE hr_checklists SET completed_at = NULL WHERE id = :c"), {"c": checklist_id})


async def due_items(conn: AsyncConnection, tenant_id: UUID, until: dt.date) -> list[dict[str, Any]]:
    """Offene, fällige Punkte (für „Zu erledigen“)."""
    rows = await conn.execute(text(
        "SELECT i.id, i.title, i.due_date, i.legal, c.kind, c.employee_id, e.first_name || ' ' || e.last_name AS employee "
        "FROM hr_checklist_items i JOIN hr_checklists c ON c.id = i.checklist_id JOIN employees e ON e.id = c.employee_id "
        "WHERE i.tenant_id = :t AND i.done_at IS NULL AND i.due_date <= :d ORDER BY i.due_date LIMIT 50"), {"t": tenant_id, "d": until})
    return [dict(r) for r in rows.mappings()]


# ───────────────────────── Vertragsvorlagen ─────────────────────────

def _md(src: str) -> str:
    """Kleines Markdown: Überschriften, Absätze, Listen, **fett** – alles escaped."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []

    def inline(s: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_lib.escape(s))

    def flush() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in src.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            level = min(3, len(s) - len(s.lstrip("#")))
            out.append(f"<h{level}>{inline(s.lstrip('#').strip())}</h{level}>")
        elif s.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(s[2:])
        else:
            para.append(s)
    flush()
    return "\n".join(out)


def _de(d: Any) -> str:
    return f"{d:%d.%m.%Y}" if d else "–"


def _money(v: Any) -> str:
    return f"{Decimal(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def placeholders(emp: dict[str, Any], company: dict[str, Any] | None, today: dt.date) -> dict[str, str]:
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    days = list(emp.get("work_days") or [0, 1, 2, 3, 4])
    pay = (f"{_money(emp['hourly_rate'])} je Stunde" if emp["salary_type"] == "hourly" else f"{_money(emp['gross_month'])} monatlich"
           if emp.get("gross_month") is not None else "–")
    rule = {"account": "auf einem Arbeitszeitkonto erfasst und durch Freizeit ausgeglichen",
            "payout": "mit dem Stundenwert vergütet", "included": "bis zu 10 % der Arbeitszeit mit der Vergütung abgegolten"}
    comp = company or {}
    return {
        "mitarbeiter.name": f"{emp['first_name']} {emp['last_name']}", "mitarbeiter.vorname": emp["first_name"],
        "mitarbeiter.nachname": emp["last_name"], "mitarbeiter.geburtsdatum": _de(emp.get("birth_date")),
        "mitarbeiter.anschrift": ", ".join(x for x in [emp.get("street") or "",
                                                       f"{emp.get('postal_code') or ''} {emp.get('city') or ''}".strip()] if x) or "–",
        "mitarbeiter.position": emp.get("position") or "Mitarbeiter/in", "mitarbeiter.personalnummer": str(emp.get("number") or ""),
        "firma.name": comp.get("legal_name", ""), "firma.ort": comp.get("city", ""),
        "firma.anschrift": f"{comp.get('street', '')}, {comp.get('postal_code', '')} {comp.get('city', '')}".strip(", "),
        "vertrag.beginn": _de(emp.get("entry_date")),
        "vertrag.befristung": f" und ist befristet bis zum {_de(emp['exit_date'])}" if emp.get("exit_date") else " und ist unbefristet",
        "vertrag.wochenstunden": str(emp.get("weekly_hours") or "–").replace(".", ","),
        "vertrag.arbeitstage": ", ".join(wd[d] for d in days), "vertrag.arbeitstage_anzahl": str(len(days)),
        "vertrag.verguetung": pay, "vertrag.urlaubstage": str(emp.get("vacation_days") or "–"),
        "vertrag.ueberstunden": rule.get(emp.get("overtime_rule") or "account", rule["account"]),
        "heute": _de(today),
    }


def render_template(body: str, values: dict[str, str]) -> tuple[str, list[str]]:
    missing: list[str] = []

    def repl(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key not in values:
            missing.append(key)
            return m.group(0)
        return values[key]
    return re.sub(r"\{\{\s*([a-z_.]+)\s*\}\}", repl, body), missing


CONTRACT_CSS = """@page { size: A4; margin: 22mm 22mm 24mm; @bottom-center { content: counter(page) " / " counter(pages);
  font-family: 'DejaVu Sans'; font-size: 8pt; color: #777; } }
body { font-family: 'DejaVu Serif', serif; font-size: 10.5pt; line-height: 1.45; color: #1e1e28; }
h1 { font-family: 'DejaVu Sans'; font-size: 18pt; margin: 0 0 6mm; }
h2 { font-family: 'DejaVu Sans'; font-size: 11pt; margin: 6mm 0 1.5mm; }
p { margin: 0 0 2.5mm; } .sign { margin-top: 18mm; display: flex; gap: 20mm; } .sign div { flex: 1; border-top: 0.6pt solid #333;
  padding-top: 1.5mm; font-size: 8.5pt; color: #555; }"""


def contract_pdf(body_md: str, signer: str, company: str) -> bytes:
    from app.einvoice.pdf import render_plain_pdf

    html = (f"<!doctype html><html lang='de'><head><meta charset='utf-8'><style>{CONTRACT_CSS}</style></head><body>{_md(body_md)}"
            f"<div class='sign'><div>{html_lib.escape(company)} (Arbeitgeber)</div><div>{html_lib.escape(signer)}</div></div>"
            "</body></html>")
    return render_plain_pdf(html)


async def contract_templates(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    await ensure_templates(conn, tenant_id)
    rows = await conn.execute(text("SELECT id, name, category, body, updated_at FROM hr_contract_templates WHERE tenant_id = :t "
                                   "ORDER BY name"),
                              {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def save_contract_template(conn: AsyncConnection, tenant_id: UUID, template_id: UUID | None, data: dict[str, Any],
                                 actor: str) -> UUID:
    if template_id is None:
        tid: UUID = await conn.scalar(text("INSERT INTO hr_contract_templates (tenant_id, name, category, body, created_by) "
                                           "VALUES (:t, :n, :c, :b, :a) RETURNING id"),
                                      {"t": tenant_id, "n": data["name"], "c": data.get("category") or "contract", "b": data["body"],
                                       "a": actor})
        return tid
    res = await conn.execute(text("UPDATE hr_contract_templates SET name = :n, category = :c, body = :b, updated_at = now() "
                                  "WHERE tenant_id = :t AND id = :id"),
                             {"t": tenant_id, "id": template_id, "n": data["name"], "c": data.get("category") or "contract",
                              "b": data["body"]})
    if not res.rowcount:
        raise NotFoundError("Vorlage nicht gefunden")
    return template_id


async def generate_contract(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, template_id: UUID, actor: str,
                            today: dt.date) -> dict[str, Any]:
    from app.services.masterdata import current_company_profile
    from app.services.payroll import get_employee

    tpl = (await conn.execute(text("SELECT name, category, body FROM hr_contract_templates WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": template_id})).mappings().one_or_none()
    if tpl is None:
        raise NotFoundError("Vorlage nicht gefunden")
    emp = await get_employee(conn, tenant_id, employee_id)
    company = await current_company_profile(conn, tenant_id, today)
    body, missing = render_template(tpl["body"], placeholders(emp, dict(company) if company else None, today))
    name = f"{emp['first_name']} {emp['last_name']}"
    pdf = contract_pdf(body, name, company["legal_name"] if company else "")
    category = tpl["category"] if tpl["category"] in DOC_CATEGORIES else "contract"
    doc_id = await upload(conn, tenant_id, employee_id, {"category": category, "title": f"{tpl['name']} – {name}",
                                                         "filename": f"{tpl['name']}-{emp['last_name']}.pdf".replace(" ", "_"),
                                                         "valid_from": emp["entry_date"], "visible_to_employee": True},
                          "application/pdf", pdf, actor)
    return {"id": doc_id, "missing": missing}


# ───────────────────────── Personalakte ─────────────────────────

DOC_COLUMNS = ("id, employee_id, category, title, filename, mime_type, size, sha256, valid_from, valid_until, visible_to_employee, "
               "signature_status, signed_sha256, delete_after, notes, created_by, created_at")


async def documents(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, *, employee_view: bool = False) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        f"SELECT {DOC_COLUMNS}, (SELECT json_build_object('id', s.id, 'status', s.status, 'channel', s.channel) FROM sign_documents s "
        "WHERE s.personnel_document_id = personnel_documents.id ORDER BY s.created_at DESC LIMIT 1) AS handoff "
        "FROM personnel_documents WHERE tenant_id = :t AND employee_id = :e AND deleted_at IS NULL "
        "AND (NOT :v OR visible_to_employee) ORDER BY created_at DESC"), {"t": tenant_id, "e": employee_id, "v": employee_view})
    return [dict(r._mapping) | {"category_label": DOC_CATEGORIES.get(r.category, r.category)} for r in rows]


async def upload(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, meta: dict[str, Any], mime: str, content: bytes,
                 actor: str) -> UUID:
    if meta["category"] not in DOC_CATEGORIES:
        raise ConflictError("Unbekannte Kategorie")
    if mime not in ALLOWED_MIME:
        raise ConflictError("Erlaubt: PDF, PNG, JPG, TXT, DOCX")
    if not content or len(content) > MAX_BYTES:
        raise ConflictError("Datei leer oder größer als 20 MB")
    exit_ = await conn.scalar(text("SELECT exit_date FROM employees WHERE tenant_id = :t AND id = :e"), {"t": tenant_id, "e": employee_id})
    delete_after = meta.get("delete_after") or (dt.date(exit_.year + 3, 12, 31) if exit_ else None)
    did: UUID = await conn.scalar(text(
        "INSERT INTO personnel_documents (tenant_id, employee_id, category, title, filename, mime_type, size, sha256, content, valid_from, "
        "valid_until, visible_to_employee, delete_after, notes, created_by) VALUES (:t, :e, :c, :ti, :f, :m, :s, :h, :b, :vf, :vu, :v, "
        ":da, :n, :a) RETURNING id"),
        {"t": tenant_id, "e": employee_id, "c": meta["category"], "ti": meta["title"][:200], "f": meta["filename"][:200], "m": mime,
         "s": len(content), "h": hashlib.sha256(content).hexdigest(), "b": content, "vf": meta.get("valid_from"),
         "vu": meta.get("valid_until"), "v": bool(meta.get("visible_to_employee")), "da": delete_after, "n": meta.get("notes"),
         "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="personnel.document_added", entity_type="employee",
                       entity_id=employee_id, after={"document": str(did), "category": meta["category"], "title": meta["title"]})
    return did


async def file(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, *, employee_id: UUID | None = None,
               signed: bool = False) -> tuple[str, str, bytes]:
    row = (await conn.execute(text(
        "SELECT filename, mime_type, content, signed_content, visible_to_employee, employee_id FROM personnel_documents "
        "WHERE tenant_id = :t AND id = :id AND deleted_at IS NULL"), {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None or (employee_id is not None and (row["employee_id"] != employee_id or not row["visible_to_employee"])):
        raise NotFoundError("Dokument nicht gefunden")
    content = row["signed_content"] if signed and row["signed_content"] else row["content"]
    if content is None:
        raise NotFoundError("Inhalt nicht mehr vorhanden")
    name = row["filename"].replace(".pdf", "-signiert.pdf") if signed and row["signed_content"] else row["filename"]
    return name, row["mime_type"], bytes(content)


async def update_document(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, data: dict[str, Any], actor: str) -> None:
    res = await conn.execute(text(
        "UPDATE personnel_documents SET title = :ti, category = :c, valid_from = :vf, valid_until = :vu, visible_to_employee = :v, "
        "delete_after = :da, notes = :n WHERE tenant_id = :t AND id = :id AND deleted_at IS NULL"),
        {"t": tenant_id, "id": doc_id, "ti": data["title"], "c": data["category"], "vf": data.get("valid_from"),
         "vu": data.get("valid_until"), "v": data["visible_to_employee"], "da": data.get("delete_after"), "n": data.get("notes")})
    if not res.rowcount:
        raise NotFoundError("Dokument nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="personnel.document_updated", entity_type="personnel_document",
                       entity_id=doc_id)


async def delete_document(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, actor: str, reason: str) -> None:
    """Löscht den Inhalt endgültig (DSGVO); Metadaten mit Hash bleiben als Nachweis, dass und wann gelöscht wurde."""
    row = (await conn.execute(text("SELECT employee_id, title, signature_status FROM personnel_documents WHERE tenant_id = :t AND id = :id "
                                   "AND deleted_at IS NULL"), {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    if row["signature_status"] == "pending":
        raise ConflictError("Signatur läuft noch")
    await conn.execute(text("UPDATE personnel_documents SET content = NULL, signed_content = NULL, deleted_at = now(), deleted_by = :a, "
                            "notes = coalesce(notes || E'\\n', '') || :r WHERE id = :id"),
                       {"id": doc_id, "a": actor, "r": f"Gelöscht: {reason}"})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="personnel.document_deleted", entity_type="employee",
                       entity_id=row["employee_id"], after={"title": row["title"], "reason": reason})


async def deletion_due(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT d.id, d.title, d.delete_after, e.first_name || ' ' || e.last_name AS employee FROM personnel_documents d "
        "JOIN employees e ON e.id = d.employee_id WHERE d.tenant_id = :t AND d.deleted_at IS NULL AND d.delete_after < :d "
        "ORDER BY d.delete_after"), {"t": tenant_id, "d": today})
    return [dict(r) for r in rows.mappings()]


# ───────────────────────── Signatur ─────────────────────────

async def signature_subject(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(
        "SELECT d.title, d.mime_type, d.content, d.signature_status, d.category, e.first_name, e.last_name, e.email, e.exit_date "
        "FROM personnel_documents d "
        "JOIN employees e ON e.id = d.employee_id WHERE d.tenant_id = :t AND d.id = :id AND d.deleted_at IS NULL"),
        {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    if row["mime_type"] != "application/pdf":
        raise ConflictError("Nur PDF-Dokumente können signiert werden")
    if row["signature_status"] in ("pending", "signed"):
        raise ConflictError("Dokument ist bereits signiert oder in Signatur")
    from app.services.sign_documents import ensure_readable_pdf

    ensure_readable_pdf(bytes(row["content"]))
    return {"title": row["title"], "reference": row["title"][:60], "b2c": False, "customer_id": None, "pdf": bytes(row["content"]),
            "signer_name": f"{row['first_name']} {row['last_name']}", "signer_email": row["email"],
            "form": signature_form_check(row["category"], row["title"], row["exit_date"])}


def signature_form_check(category: str, title: str, exit_date: Any) -> Any:
    """Formvorschrift je Dokumentart der Personalakte (Kündigung blockiert, Befristung nur mit Bestätigung)."""
    from app.domain.signature_form import check

    kind = {"termination": "termination", "amendment": "amendment"}.get(category, "general")
    if category == "contract":
        kind = "employment_fixed_term" if exit_date else "employment"
    return check(kind, title)


async def mark_signature(conn: AsyncConnection, doc_id: UUID, status: str, signed_pdf: bytes | None = None) -> None:
    await conn.execute(text("UPDATE personnel_documents SET signature_status = :s, signed_content = coalesce(:p, signed_content), "
                            "signed_sha256 = coalesce(:h, signed_sha256) WHERE id = :id"),
                       {"s": status, "p": signed_pdf, "h": hashlib.sha256(signed_pdf).hexdigest() if signed_pdf else None, "id": doc_id})
