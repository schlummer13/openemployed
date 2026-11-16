"""Meldewesen der Lohnabrechnung: SV-Meldungen (DEÜV), Beitragsnachweise, Lohnsteuerbescheinigungen (§ 41b EStG).

Übermittelt werden diese Meldungen über eine zertifizierte Schnittstelle (SV: ITSG-geprüfte Software oder das kostenlose
SV-Meldeportal/sv.net; LStB: ELSTER über ERiC). OpenEmployed erzeugt alle Daten, Fristen und Ausdrucke und führt Buch, was
gemeldet ist – die Übermittlung selbst erfolgt im SV-Meldeportal bzw. über das Lohnbüro, bis eine Zertifizierung vorliegt.

- DEÜV-Gründe: 10 Anmeldung, 30 Abmeldung, 34 Ende wegen unbezahltem Urlaub > 1 Monat, 50 Jahresmeldung, 51 Unterbrechung
  (Krankengeld, Mutterschaftsgeld), 52 Unterbrechung (Elternzeit). Fristen nach §§ 6–12 DEÜV.
- Beitragsnachweis je Einzugsstelle: fällig am drittletzten Bankarbeitstag des Monats (§ 23 Abs. 1 SGB IV), Übermittlung
  spätestens zwei Arbeitstage vorher (0 Uhr des fünftletzten Bankarbeitstags).
- Lohnsteuerbescheinigung: bis 28.2. des Folgejahres (§ 41b Abs. 1 EStG); Zeilennummern nach dem amtlichen Ausdruck.
"""

# ruff: noqa: E501 – HTML-/SQL-Vorlagen mit amtlichen Zeilentexten
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.worktime import easter
from app.services import audit
from app.services.masterdata import NotFoundError

D = Decimal
REASONS = {"10": "Anmeldung (Beginn der Beschäftigung)", "30": "Abmeldung (Ende der Beschäftigung)",
           "34": "Ende wegen unbezahltem Urlaub über einen Monat", "50": "Jahresmeldung", "51": "Unterbrechung (Kranken-/Mutterschaftsgeld)",
           "52": "Unterbrechung (Elternzeit)"}


def bank_holidays(year: int) -> set[dt.date]:
    e = easter(year)
    return {dt.date(year, 1, 1), e - dt.timedelta(days=2), e + dt.timedelta(days=1), dt.date(year, 5, 1), e + dt.timedelta(days=39),
            e + dt.timedelta(days=50), dt.date(year, 10, 3), dt.date(year, 12, 24), dt.date(year, 12, 25), dt.date(year, 12, 26),
            dt.date(year, 12, 31)}


def nth_last_bank_day(year: int, month: int, n: int) -> dt.date:
    d = (dt.date(year, month, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    hol = bank_holidays(year)
    found = 0
    while True:
        if d.weekday() < 5 and d not in hol:
            found += 1
            if found == n:
                return d
        d -= dt.timedelta(days=1)


def _add_month(d: dt.date) -> dt.date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return d.replace(year=y, month=m, day=min(d.day, 28))


async def _employer_number(conn: AsyncConnection, tenant_id: UUID) -> str | None:
    value: str | None = await conn.scalar(text("SELECT employer_number FROM payroll_settings WHERE tenant_id = :t"), {"t": tenant_id})
    return value


def _person(e: dict[str, Any]) -> dict[str, Any]:
    return {"name": f"{e['first_name']} {e['last_name']}", "first_name": e["first_name"], "last_name": e["last_name"],
            "birth_date": str(e.get("birth_date") or ""), "birth_name": e.get("birth_name"), "birth_place": e.get("birth_place"),
            "nationality": e.get("nationality") or "DE", "street": e.get("street"), "postal_code": e.get("postal_code"),
            "city": e.get("city"), "ssn": e.get("social_security_number"), "activity_key": e.get("activity_key"),
            "insurer": "Minijob-Zentrale (Knappschaft-Bahn-See)" if e["employment_type"] == "minijob" else e.get("health_insurer")}


async def _upsert(conn: AsyncConnection, tenant_id: UUID, e: dict[str, Any], reason: str, start: dt.date, end: dt.date | None,
                  due: dt.date, wage: D | None, extra: dict[str, Any]) -> bool:
    from app.domain.payroll import PERSON_GROUPS, contribution_group_key

    data = _person(e) | {"employer_number": await _employer_number(conn, tenant_id),
                         "person_group": PERSON_GROUPS.get(e["employment_type"], ("", ""))[0],
                         "contribution_group": contribution_group_key(e["employment_type"], health_insurance=e["health_insurance"],
                                                                      rv_exempt=e["rv_exempt"]), **extra}
    res = await conn.execute(text(
        "INSERT INTO sv_reports (tenant_id, employee_id, reason, period_from, period_to, sv_wage, data, due_date) "
        "VALUES (:t, :e, :r, :f, :to, :w, CAST(:d AS jsonb), :due) ON CONFLICT (employee_id, reason, period_from) DO UPDATE SET "
        "period_to = EXCLUDED.period_to, sv_wage = EXCLUDED.sv_wage, data = EXCLUDED.data WHERE sv_reports.status = 'open'"),
        {"t": tenant_id, "e": e["id"], "r": reason, "f": start, "to": end, "w": wage, "d": json.dumps(data, default=str), "due": due})
    return bool(res.rowcount)


async def _sv_wage(conn: AsyncConnection, employee_id: UUID, start: dt.date, end: dt.date) -> D:
    v = await conn.scalar(text(
        "SELECT coalesce(sum(CAST(p.details->>'sv_gross_rv' AS numeric)), 0) FROM payslips p JOIN payroll_runs r ON r.id = p.run_id "
        "WHERE p.employee_id = :e AND r.status = 'approved' AND r.period >= :a AND r.period <= :b"),
        {"e": employee_id, "a": f"{start:%Y-%m}", "b": f"{end:%Y-%m}"})
    return D(v or 0).quantize(D("1"))


async def refresh_sv_reports(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> int:
    """Legt fällige DEÜV-Meldungen an (idempotent)."""
    emps = [dict(r) for r in (await conn.execute(text(
        "SELECT * FROM employees WHERE tenant_id = :t AND (archived_at IS NULL OR exit_date >= :y0)"),
        {"t": tenant_id, "y0": dt.date(today.year - 1, 1, 1)})).mappings()]
    created = 0
    # Ein- und Austritte vor dem ersten Lohnlauf in OpenEmployed gelten als bereits gemeldet (Umstieg aus einem anderen System)
    first_run = await conn.scalar(text("SELECT min(period) FROM payroll_runs WHERE tenant_id = :t"), {"t": tenant_id})
    if first_run is None:
        return 0
    since = dt.date.fromisoformat(f"{first_run}-01")
    await conn.execute(text("DELETE FROM sv_reports WHERE tenant_id = :t AND status = 'open' AND reason IN ('10', '30') AND "
                            "coalesce(period_to, period_from) < :s AND (reason = '30' OR period_from < :s)"), {"t": tenant_id, "s": since})
    for e in emps:
        if since <= e["entry_date"] <= today + dt.timedelta(days=30):
            created += await _upsert(conn, tenant_id, e, "10", e["entry_date"], None, e["entry_date"] + dt.timedelta(days=42), None, {})
        if e.get("exit_date") and since <= e["exit_date"] <= today + dt.timedelta(days=31):
            start = max(e["entry_date"], dt.date(e["exit_date"].year, 1, 1))
            created += await _upsert(conn, tenant_id, e, "30", start, e["exit_date"], e["exit_date"] + dt.timedelta(days=42),
                                     await _sv_wage(conn, e["id"], start, e["exit_date"]), {})
        prev = today.year - 1
        if since <= dt.date(prev, 12, 31) and e["entry_date"] <= dt.date(prev, 12, 31) and (not e.get("exit_date") or e["exit_date"] > dt.date(prev, 12, 31)):
            start = max(e["entry_date"], dt.date(prev, 1, 1))
            wage = await _sv_wage(conn, e["id"], start, dt.date(prev, 12, 31))
            if wage:
                created += await _upsert(conn, tenant_id, e, "50", start, dt.date(prev, 12, 31), dt.date(today.year, 2, 15), wage, {})
    # Unterbrechungen aus freigegebenen Abrechnungen
    rows = await conn.execute(text(
        "SELECT p.employee_id, x FROM payslips p JOIN payroll_runs r ON r.id = p.run_id, jsonb_array_elements(coalesce(p.details->'sv_reports', "
        "'[]')) x WHERE p.tenant_id = :t AND r.status = 'approved'"), {"t": tenant_id})
    by_id = {e["id"]: e for e in emps}
    for emp_id, rep in rows.all():
        emp = by_id.get(emp_id)
        if not emp:
            continue
        reason, start = rep[0], dt.date.fromisoformat(rep[1])
        end = dt.date.fromisoformat(rep[2]) if rep[2] else None
        due = _add_month(start) + dt.timedelta(days=14) if reason in ("51", "52") else start + dt.timedelta(days=42)
        created += await _upsert(conn, tenant_id, emp, reason, start, end, due, None, {})
    return created


async def sv_reports(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> dict[str, Any]:
    await refresh_sv_reports(conn, tenant_id, today)
    rows = await conn.execute(text(
        "SELECT id, employee_id, reason, period_from, period_to, sv_wage, data, due_date, status, submitted_at, submitted_by, reference "
        "FROM sv_reports WHERE tenant_id = :t ORDER BY status = 'open' DESC, due_date"), {"t": tenant_id})
    items = [dict(r._mapping) | {"reason_label": REASONS.get(r.reason, r.reason)} for r in rows]
    return {"reports": items, "employer_number": await _employer_number(conn, tenant_id), "reasons": REASONS}


async def mark_report(conn: AsyncConnection, tenant_id: UUID, report_id: UUID, status: str, reference: str | None, actor: str) -> None:
    res = await conn.execute(text("UPDATE sv_reports SET status = :s, reference = :r, submitted_at = CASE WHEN :s = 'submitted' THEN now() END, "
                                  "submitted_by = CASE WHEN :s = 'submitted' THEN :a END WHERE tenant_id = :t AND id = :id"),
                             {"t": tenant_id, "id": report_id, "s": status, "r": reference, "a": actor})
    if not res.rowcount:
        raise NotFoundError("Meldung nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"sv_report.{status}", entity_type="sv_report", entity_id=report_id,
                       after={"reference": reference})


# ───────────────────────── Beitragsnachweise ─────────────────────────

async def statements(conn: AsyncConnection, tenant_id: UUID, period: str) -> list[dict[str, Any]]:
    """Beitragsnachweise je Einzugsstelle aus dem Lohnlauf des Monats (Entwurf oder freigegeben)."""
    y, m = (int(x) for x in period.split("-"))
    rows = (await conn.execute(text(
        "SELECT p.employment_type, p.employee_id, p.kv_ee, p.kv_er, p.rv_ee, p.rv_er, p.av_ee, p.av_er, p.pv_ee, p.pv_er, p.levies, "
        "p.flat_tax, p.details FROM payslips p JOIN payroll_runs r ON r.id = p.run_id WHERE r.tenant_id = :t AND r.period = :p "
        "AND p.employee_id IS NOT NULL"), {"t": tenant_id, "p": period})).mappings().all()
    groups: dict[str, dict[str, D]] = {}
    for r in rows:
        det = r["details"] or {}
        if r["employment_type"] == "minijob":
            ins = "Minijob-Zentrale (Knappschaft-Bahn-See)"
            g = groups.setdefault(ins, {})
            g["KV pauschal (13 %)"] = g.get("KV pauschal (13 %)", D(0)) + D(r["kv_er"])
            g["RV pauschal (15 %)"] = g.get("RV pauschal (15 %)", D(0)) + D(r["rv_er"])
            g["RV Aufstockung AN"] = g.get("RV Aufstockung AN", D(0)) + D(r["rv_ee"])
            g["Pauschsteuer (2 %)"] = g.get("Pauschsteuer (2 %)", D(0)) + D(r["flat_tax"])
            g["Umlagen U1/U2/Insolvenzgeld"] = g.get("Umlagen U1/U2/Insolvenzgeld", D(0)) + D(r["levies"])
            continue
        ins = det.get("health_insurer") or ("Krankenkasse für RV/AV wählen" if det.get("health_insurance") == "pkv" else "Krankenkasse fehlt")
        g = groups.setdefault(ins, {})
        for label, val in (("Krankenversicherung (inkl. Zusatzbeitrag)", D(r["kv_ee"]) + D(r["kv_er"])),
                           ("Rentenversicherung", D(r["rv_ee"]) + D(r["rv_er"])), ("Arbeitslosenversicherung", D(r["av_ee"]) + D(r["av_er"])),
                           ("Pflegeversicherung", D(r["pv_ee"]) + D(r["pv_er"])), ("Umlagen U1/U2/Insolvenzgeld", D(r["levies"]))):
            if val:
                g[label] = g.get(label, D(0)) + val
    due = nth_last_bank_day(y, m, 3)
    submit_by = nth_last_bank_day(y, m, 5)
    out = []
    for ins, vals in sorted(groups.items()):
        total = sum(vals.values(), D(0))
        await conn.execute(text(
            "INSERT INTO contribution_statements (tenant_id, period, insurer, data, total, submit_by, due_date) VALUES (:t, :p, :i, "
            "CAST(:d AS jsonb), :tot, :sb, :due) ON CONFLICT (tenant_id, period, insurer) DO UPDATE SET data = EXCLUDED.data, "
            "total = EXCLUDED.total WHERE contribution_statements.status = 'open'"),
            {"t": tenant_id, "p": period, "i": ins, "d": json.dumps({k: str(v) for k, v in vals.items()}), "tot": total, "sb": submit_by, "due": due})
    rows2 = await conn.execute(text("SELECT id, insurer, data, total, submit_by, due_date, status, submitted_at FROM contribution_statements "
                                    "WHERE tenant_id = :t AND period = :p ORDER BY insurer"), {"t": tenant_id, "p": period})
    for r in rows2.mappings():
        out.append(dict(r))
    return out


async def mark_statement(conn: AsyncConnection, tenant_id: UUID, statement_id: UUID, actor: str) -> None:
    res = await conn.execute(text("UPDATE contribution_statements SET status = 'submitted', submitted_at = now(), submitted_by = :a "
                                  "WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": statement_id, "a": actor})
    if not res.rowcount:
        raise NotFoundError("Beitragsnachweis nicht gefunden")


# ───────────────────────── Lohnsteuerbescheinigung ─────────────────────────

async def build_certificates(conn: AsyncConnection, tenant_id: UUID, year: int, actor: str) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT p.employee_id, r.period, p.employment_type, p.gross, p.lst, p.solz, p.church, p.rv_ee, p.rv_er, p.kv_ee, p.pv_ee, p.av_ee, "
        "p.pkv_subsidy, p.details FROM payslips p JOIN payroll_runs r ON r.id = p.run_id WHERE r.tenant_id = :t AND r.status = 'approved' "
        "AND r.period LIKE :y AND p.employee_id IS NOT NULL ORDER BY r.period"), {"t": tenant_id, "y": f"{year}-%"})).mappings().all()
    per: dict[UUID, list[dict[str, Any]]] = {}
    for r in rows:
        if r["employment_type"] != "minijob":  # Minijobs mit Pauschsteuer: keine Lohnsteuerbescheinigung
            per.setdefault(r["employee_id"], []).append(dict(r))
    out = []
    for emp_id, slips in per.items():
        e = dict((await conn.execute(text("SELECT * FROM employees WHERE id = :id"), {"id": emp_id})).mappings().one())
        first = dt.date.fromisoformat(slips[0]["period"] + "-01")
        last_p = dt.date.fromisoformat(slips[-1]["period"] + "-01")
        start = max(first, e["entry_date"])
        end_month = (last_p + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        end = min(end_month, e["exit_date"]) if e.get("exit_date") else end_month
        u_months = sum(1 for s in slips if (s["details"] or {}).get("sv_days") == 0 and s["employment_type"] != "shareholder")

        def total(key: str, sl: list[dict[str, Any]] = slips) -> str:
            return str(sum((D(s[key]) for s in sl), D(0)))

        maternity = sum((D(ln["amount"]) for s in slips for ln in (s["details"] or {}).get("lines", [])
                         if ln.get("type") == "tax_free" and "Mutterschaftsgeld" in ln.get("label", "")), D(0))
        data = {
            "employee": {"name": f"{e['first_name']} {e['last_name']}", "street": e.get("street"), "postal_code": e.get("postal_code"),
                         "city": e.get("city"), "birth_date": str(e.get("birth_date") or ""), "tax_id": e.get("tax_id"),
                         "number": e["number"], "tax_class": e["tax_class"], "child_allowances": str(e["child_allowances"]),
                         "church": e["church_denomination"] if e["church_tax"] else None},
            "1": f"{start:%d.%m.} – {end:%d.%m.}", "2": f"{u_months} × U" if u_months else "",
            "3": total("gross"), "4": total("lst"), "5": total("solz"), "6": total("church"),
            "15": str(maternity), "22a": total("rv_er"), "23a": total("rv_ee"), "24a": total("pkv_subsidy"),
            "25": total("kv_ee"), "26": total("pv_ee"), "27": total("av_ee"),
            "28": str(D(e.get("pkv_premium") or 0) * len(slips)) if e.get("health_insurance") == "pkv" else "0",
            "months": len(slips), "year": year,
        }
        await conn.execute(text(
            "INSERT INTO wage_tax_certificates (tenant_id, employee_id, year, data) VALUES (:t, :e, :y, CAST(:d AS jsonb)) "
            "ON CONFLICT (employee_id, year) DO UPDATE SET data = EXCLUDED.data WHERE wage_tax_certificates.status = 'draft'"),
            {"t": tenant_id, "e": emp_id, "y": year, "d": json.dumps(data, default=str)})
        out.append({"employee_id": emp_id} | data)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="lstb.built", entity_type="payroll", entity_id=None,
                       after={"year": year, "count": len(out)})
    return out


async def certificates(conn: AsyncConnection, tenant_id: UUID, year: int) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT id, employee_id, year, data, status, transmitted_at, ticket, created_at FROM wage_tax_certificates "
                                   "WHERE tenant_id = :t AND year = :y ORDER BY data->'employee'->>'name'"), {"t": tenant_id, "y": year})
    return [dict(r) for r in rows.mappings()]


def certificate_html(c: dict[str, Any], company: dict[str, Any] | None) -> str:
    from html import escape

    d = c["data"]
    emp = d["employee"]

    def eur(v: Any) -> str:
        return f"{D(str(v or 0)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    lines = [("1", "Dauer des Dienstverhältnisses", d["1"], False), ("2", "Zeiträume ohne Anspruch auf Arbeitslohn (Anzahl „U“)", d["2"] or "–", False),
             ("3", "Bruttoarbeitslohn einschl. Sachbezüge", d["3"], True), ("4", "Einbehaltene Lohnsteuer von 3.", d["4"], True),
             ("5", "Einbehaltener Solidaritätszuschlag von 3.", d["5"], True), ("6", "Einbehaltene Kirchensteuer des Arbeitnehmers von 3.", d["6"], True),
             ("15", "Kurzarbeitergeld, Zuschuss zum Mutterschaftsgeld …", d["15"], True),
             ("22a", "Arbeitgeberanteil zur gesetzlichen Rentenversicherung", d["22a"], True),
             ("23a", "Arbeitnehmeranteil zur gesetzlichen Rentenversicherung", d["23a"], True),
             ("24a", "Steuerfreie Arbeitgeberzuschüsse zur Krankenversicherung (privat)", d["24a"], True),
             ("25", "Arbeitnehmerbeiträge zur gesetzlichen Krankenversicherung", d["25"], True),
             ("26", "Arbeitnehmerbeiträge zur sozialen Pflegeversicherung", d["26"], True),
             ("27", "Arbeitnehmerbeiträge zur Arbeitslosenversicherung", d["27"], True),
             ("28", "Beiträge zur privaten Kranken- und Pflege-Pflichtversicherung", d["28"], True)]
    rows = "".join(f"<tr><td class='n'>{n}.</td><td>{escape(label)}</td><td class='r'>{eur(v) if money else escape(str(v))}</td></tr>"
                   for n, label, v, money in lines)
    comp = f"{escape(company['legal_name'])}, {escape(company['street'])}, {escape(company['postal_code'])} {escape(company['city'])}" if company else ""
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 16mm; }} body {{ font-family: 'DejaVu Sans'; font-size: 9pt; color: #1e1e28; }}
h1 {{ font-size: 13pt; margin: 0 0 1mm; }} table {{ width: 100%; border-collapse: collapse; margin-top: 4mm; }}
td {{ border-bottom: 0.3pt solid #ddd; padding: 1.4mm 1mm; }} .n {{ width: 10mm; color: #666; }} .r {{ text-align: right; width: 35mm; }}
.box {{ border: 0.5pt solid #999; padding: 3mm; margin-top: 4mm; }} .muted {{ color: #666; font-size: 7.8pt; }}
</style></head><body>
<h1>Ausdruck der elektronischen Lohnsteuerbescheinigung für {d['year']}</h1>
<div class="muted">nach § 41b EStG · Arbeitgeber: {comp}</div>
<div class="box"><b>{escape(emp['name'])}</b><br>{escape(emp.get('street') or '')}<br>{escape(emp.get('postal_code') or '')} {escape(emp.get('city') or '')}<br>
Geburtsdatum {escape(emp.get('birth_date') or '–')} · Identifikationsnummer {escape(emp.get('tax_id') or '–')} · Personalnummer {emp.get('number')}<br>
Steuerklasse {emp.get('tax_class')} · Kinderfreibeträge {escape(str(emp.get('child_allowances')))} · Kirchensteuermerkmal {escape(emp.get('church') or '–')}</div>
<table>{rows}</table>
<p class="muted" style="margin-top:6mm">Status: {"übermittelt" if c.get("status") == "transmitted" else "Entwurf – noch nicht an das Finanzamt übermittelt"}{f" · Transferticket {escape(c['ticket'])}" if c.get('ticket') else ""}.
Alle Beträge in Euro. Erstellt mit OpenEmployed.</p></body></html>"""


async def certificate_pdf(conn: AsyncConnection, tenant_id: UUID, cert_id: UUID) -> tuple[str, bytes]:
    from app.einvoice.pdf import render_plain_pdf
    from app.services.invoices import today_berlin
    from app.services.masterdata import current_company_profile

    row = (await conn.execute(text("SELECT id, employee_id, year, data, status, ticket FROM wage_tax_certificates WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": cert_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Lohnsteuerbescheinigung nicht gefunden")
    company = await current_company_profile(conn, tenant_id, today_berlin())
    c = dict(row)
    return f"Lohnsteuerbescheinigung-{c['year']}-{c['data']['employee']['name'].replace(' ', '_')}.pdf", render_plain_pdf(
        certificate_html(c, dict(company) if company else None))
