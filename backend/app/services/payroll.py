"""Lohnbuchhaltung: Mitarbeiterstamm, monatlicher Lohnlauf, Lohnbuchung, Auszahlung und Lohnsteuer-Anmeldung.

Ablauf: Lohnlauf (Entwurf, jederzeit neu berechenbar) → Freigabe (Abrechnungen unveränderlich, Lohnbuchung festgeschrieben)
→ Zahlungen (Nettolöhne, Lohnsteuer ans Finanzamt, SV an die Krankenkassen) als Bankbuchungen.
Die Berechnung ist eine Näherung (app/domain/payroll.py) – vor dem Produktivstart mit dem Lohnbüro abgleichen.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import uuid
from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.payroll import (
    MIDIJOB_UPPER_2026,
    MINIJOB_LIMIT_2026,
    MINIMUM_WAGE_2026,
    PARAMS_2026,
    PERSON_GROUPS,
    Payslip,
    TaxProfile,
    contribution_group_key,
    midijob_bases,
    payslip,
)
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
ZERO = D("0.00")
PAYROLL_NS = uuid.UUID("5e1d7c0a-3b7f-4f43-9d2c-8a1b6e4f0c21")

EMPLOYEE_FIELDS = (
    "first_name", "last_name", "email", "phone", "birth_date", "street", "postal_code", "city", "position", "entry_date",
    "exit_date", "employment_type", "salary_type", "gross_month", "hourly_rate", "weekly_hours", "salaries_per_year",
    "vacation_days", "tax_id", "tax_class", "child_allowances", "church_tax", "church_denomination", "state",
    "social_security_number", "health_insurance", "health_insurer", "kv_additional_rate", "pkv_premium", "children_under_25",
    "rv_exempt", "iban", "account_holder", "portal_enabled", "notes", "work_days", "vacation_carryover",
    "time_account_start", "overtime_rule", "overtime_cap_hours", "tax_factor", "tax_allowance_month", "tax_addition_month",
    "activity_key", "nationality", "birth_name", "birth_place", "car_list_price", "car_distance_km", "car_kind", "car_copayment",
)
SELF_SERVICE_FIELDS = ("phone", "street", "postal_code", "city", "iban", "account_holder")
SLIP_COLUMNS = ("id, run_id, employee_id, shareholder_id, name, employment_type, hours, gross, lst, solz, church, rv_ee, av_ee, "
                "kv_ee, pv_ee, rv_er, av_er, kv_er, pv_er, levies, flat_tax, pkv_subsidy, net, payout, employer_cost, iban, details")
WEEKS_PER_MONTH = D(13) / D(3)


def _cent(x: D) -> D:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def period_bounds(period: str) -> tuple[dt.date, dt.date]:
    try:
        y, m = (int(x) for x in period.split("-"))
        first = dt.date(y, m, 1)
    except ValueError as exc:
        raise ConflictError("Zeitraum im Format JJJJ-MM") from exc
    return first, first.replace(day=calendar.monthrange(y, m)[1])


# ───────────────────────── Mitarbeiterstamm ─────────────────────────

def _check(data: dict[str, Any]) -> list[str]:
    """Plausibilitätsprüfung; Rückgabe = Hinweise (keine Fehler)."""
    hints: list[str] = []
    if data["salary_type"] == "monthly" and data.get("gross_month") is None:
        raise ConflictError("Monatsgehalt fehlt")
    if data["salary_type"] == "hourly" and data.get("hourly_rate") is None:
        raise ConflictError("Stundenlohn fehlt")
    if data.get("exit_date") and data["exit_date"] < data["entry_date"]:
        raise ConflictError("Austritt liegt vor dem Eintritt")
    if data["salary_type"] == "hourly" and D(data["hourly_rate"]) < MINIMUM_WAGE_2026:
        raise ConflictError(f"Stundenlohn unter dem gesetzlichen Mindestlohn ({MINIMUM_WAGE_2026} €)")
    if data["salary_type"] == "monthly" and data.get("weekly_hours"):
        per_hour = D(data["gross_month"]) / (D(data["weekly_hours"]) * WEEKS_PER_MONTH)
        if per_hour < MINIMUM_WAGE_2026:
            raise ConflictError(f"Gehalt entspricht {_cent(per_hour)} €/Std. – unter dem Mindestlohn ({MINIMUM_WAGE_2026} €)")
    if data["employment_type"] == "minijob":
        expected = _expected_gross(data)
        if expected is not None and expected > MINIJOB_LIMIT_2026:
            raise ConflictError(f"Minijob über der Geringfügigkeitsgrenze ({MINIJOB_LIMIT_2026} €/Monat)")
    if data["employment_type"] == "regular":
        expected = _expected_gross(data)
        if expected is not None and MINIJOB_LIMIT_2026 < expected <= MIDIJOB_UPPER_2026:
            hints.append("Übergangsbereich (Midijob): reduzierte AN-Beiträge nach § 20a SGB IV werden automatisch berechnet")
    if data.get("portal_enabled") and not data.get("email"):
        raise ConflictError("Für den Mitarbeiter-Zugang wird eine E-Mail-Adresse benötigt")
    if data["health_insurance"] == "gkv":
        data["pkv_premium"] = ZERO
    if not data.get("iban"):
        hints.append("IBAN fehlt – keine Überweisung möglich")
    if not data.get("tax_id"):
        hints.append("Steuer-ID fehlt (ELStAM-Abruf)")
    return hints


def _expected_gross(data: dict[str, Any]) -> D | None:
    if data["salary_type"] == "monthly":
        return D(data["gross_month"])
    if data.get("weekly_hours"):
        return _cent(D(data["hourly_rate"]) * D(data["weekly_hours"]) * WEEKS_PER_MONTH)
    return None


async def list_employees(conn: AsyncConnection, tenant_id: UUID, *, archived: bool = False) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        f"SELECT id, number, {', '.join(EMPLOYEE_FIELDS)}, archived_at, updated_at FROM employees WHERE tenant_id = :t "
        f"AND (archived_at IS {'NOT ' if archived else ''}NULL) ORDER BY last_name, first_name"), {"t": tenant_id})
    out = []
    for r in rows.mappings():
        item = dict(r)
        try:
            item["hints"] = _check(dict(r))
        except ConflictError as exc:
            item["hints"] = [str(exc)]
        out.append(item)
    return out


async def get_employee(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(
        f"SELECT id, number, {', '.join(EMPLOYEE_FIELDS)}, archived_at, updated_at FROM employees WHERE tenant_id = :t AND id = :id"),
        {"t": tenant_id, "id": employee_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Mitarbeiter nicht gefunden")
    return dict(row)


async def save_employee(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str,
                        employee_id: UUID | None = None) -> tuple[UUID, list[str]]:
    data = {k: data.get(k) for k in EMPLOYEE_FIELDS}
    if data.get("email"):
        data["email"] = data["email"].strip().lower()
    if data.get("iban"):
        data["iban"] = data["iban"].replace(" ", "").upper()
    hints = _check(data)
    if data.get("email"):
        dup = await conn.scalar(text("SELECT 1 FROM employees WHERE tenant_id = :t AND email = :e AND archived_at IS NULL "
                                     "AND id IS DISTINCT FROM :id"), {"t": tenant_id, "e": data["email"], "id": employee_id})
        if dup:
            raise ConflictError("Diese E-Mail-Adresse gehört bereits zu einem anderen Mitarbeiter")
    params = data | {"t": tenant_id, "id": employee_id, "a": actor}
    if employee_id is None:
        number = await conn.scalar(text("SELECT COALESCE(max(number), 0) + 1 FROM employees WHERE tenant_id = :t"), {"t": tenant_id})
        employee_id = await conn.scalar(text(
            f"INSERT INTO employees (tenant_id, number, {', '.join(EMPLOYEE_FIELDS)}, created_by) "
            f"VALUES (:t, :n, {', '.join(':' + f for f in EMPLOYEE_FIELDS)}, :a) RETURNING id"), params | {"n": number})
        action = "employee.created"
        old_exit = None
    else:
        old_exit = (await get_employee(conn, tenant_id, employee_id)).get("exit_date")
        await conn.execute(text(
            f"UPDATE employees SET {', '.join(f'{f} = :{f}' for f in EMPLOYEE_FIELDS)}, updated_at = now() "
            "WHERE tenant_id = :t AND id = :id"), params)
        action = "employee.updated"
    redacted = {k: ("***" if k in ("tax_id", "social_security_number", "iban") and v else v) for k, v in data.items()}
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="employee", entity_id=employee_id,
                       after={k: str(v) if v is not None else None for k, v in redacted.items()})
    from app.services.hr import on_employee_saved

    await on_employee_saved(conn, tenant_id, employee_id, created=action == "employee.created", entry=data["entry_date"],
                            exit_=data.get("exit_date"), old_exit=old_exit, actor=actor)
    return employee_id, hints


async def archive_employee(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, actor: str) -> None:
    await get_employee(conn, tenant_id, employee_id)
    await conn.execute(text("UPDATE employees SET archived_at = now(), portal_enabled = false WHERE tenant_id = :t AND id = :id"),
                       {"t": tenant_id, "id": employee_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="employee.archived", entity_type="employee",
                       entity_id=employee_id)


def employee_profile(e: dict[str, Any]) -> TaxProfile:
    return TaxProfile(tax_class=e["tax_class"], child_allowances=D(e["child_allowances"]), church_tax=e["church_tax"],
                      state=e["state"], birth_year=e["birth_date"].year if e.get("birth_date") else None,
                      children_under_25=e["children_under_25"], health_insurance=e["health_insurance"],
                      kv_additional_rate=D(e["kv_additional_rate"]) if e.get("kv_additional_rate") is not None else None,
                      pkv_premium=D(e["pkv_premium"] or 0),
                      factor=D(e["tax_factor"]) if e.get("tax_factor") and e["tax_class"] == 4 else None,
                      allowance_month=D(e.get("tax_allowance_month") or 0), addition_month=D(e.get("tax_addition_month") or 0))


# ───────────────────────── Lohnlauf ─────────────────────────

def _employee_gross(e: dict[str, Any], first: dt.date, last: dt.date, hours: D | None,
                    clocked: D | None = None) -> tuple[D, D | None, list[str]]:
    notes: list[str] = []
    if e["salary_type"] == "hourly":
        if hours is None and clocked:
            hours = clocked
            notes.append("Stunden aus der Stempeluhr (netto nach gesetzlicher Pause)")
        if hours is None:
            hours = _cent(D(e["weekly_hours"]) * WEEKS_PER_MONTH) if e.get("weekly_hours") else ZERO
            notes.append("Stunden aus der Wochenarbeitszeit geschätzt – tatsächliche Stunden eintragen")
        return _cent(D(e["hourly_rate"]) * hours), hours, notes
    gross = D(e["gross_month"])
    start = max(first, e["entry_date"])
    end = min(last, e["exit_date"]) if e.get("exit_date") else last
    days = (end - start).days + 1
    month_days = (last - first).days + 1
    if days < month_days:
        notes.append(f"Anteilig für {days} von {month_days} Kalendertagen (Ein-/Austritt)")
        gross = _cent(gross * days / month_days)
    return gross, None, notes


def legal_details(e: dict[str, Any], gross: D, hours: D | None, first: dt.date, last: dt.date) -> dict[str, Any]:
    """Pflichtangaben der Entgeltbescheinigung (§ 108 Abs. 3 GewO, EBV) – zum Abrechnungszeitpunkt festgehalten."""
    kind = e["employment_type"]
    start = max(first, e["entry_date"])
    end = min(last, e["exit_date"]) if e.get("exit_date") else last
    days = (end - start).days + 1
    full_month = days == (last - first).days + 1
    sv_days = 30 if full_month else min(30, days)
    midi = midijob_bases(gross) if kind == "regular" else None
    if kind == "minijob":
        sv_rv = sv_kv = gross
    else:
        sv_rv = midi.total_base if midi else min(gross, PARAMS_2026.bbg_rv_month)
        sv_kv = (midi.total_base if midi else min(gross, PARAMS_2026.bbg_kv_month)) if kind != "werkstudent" else ZERO
    lines: list[dict[str, Any]]
    if e["salary_type"] == "hourly":
        lines = [{"label": "Stundenlohn", "quantity": str(hours or ZERO), "unit": "Std.", "rate": str(e["hourly_rate"]),
                  "amount": str(gross),
                  "kind": "laufend"}]
    else:
        lines = [{"label": "Gehalt" + ("" if full_month else f" (anteilig {days}/{(last - first).days + 1} Kalendertage)"),
                  "quantity": None, "unit": None, "rate": str(e["gross_month"]), "amount": str(gross), "kind": "laufend"}]
    pg = PERSON_GROUPS.get(kind, ("", ""))
    return {
        "birth_date": e["birth_date"].isoformat() if e.get("birth_date") else None,
        "social_security_number": e.get("social_security_number"),
        "entry_date": e["entry_date"].isoformat(), "exit_date": e["exit_date"].isoformat() if e.get("exit_date") else None,
        "child_allowances": str(e["child_allowances"]), "state": e["state"], "health_insurance": e["health_insurance"],
        "salary_type": e["salary_type"], "salaries_per_year": e["salaries_per_year"],
        "person_group": pg[0], "person_group_label": pg[1],
        "contribution_group": contribution_group_key(kind, health_insurance=e["health_insurance"], rv_exempt=e["rv_exempt"]),
        "sv_days": sv_days, "tax_days": days,
        "sv_gross_rv": str(_cent(sv_rv)), "sv_gross_kv": str(_cent(sv_kv)), "tax_gross": str(ZERO if kind == "minijob" else gross),
        "midijob": ({"factor": str(midi.factor), "total_base": str(midi.total_base), "employee_base": str(midi.employee_base)}
                    if midi else None),
        "lines": lines,
    }


def _slip_row(p: Payslip) -> dict[str, Any]:
    return {k: v for k, v in asdict(p).items() if k not in ("notes", "pkv_premium", "one_time", "benefits", "tax_free", "deductions",
                                                            "sv_base_rv", "sv_base_kv")}


async def _active_employees(conn: AsyncConnection, tenant_id: UUID, first: dt.date, last: dt.date) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        f"SELECT id, number, {', '.join(EMPLOYEE_FIELDS)} FROM employees WHERE tenant_id = :t AND archived_at IS NULL "
        "AND entry_date <= :last AND (exit_date IS NULL OR exit_date >= :first) ORDER BY last_name, first_name"),
        {"t": tenant_id, "first": first, "last": last})
    return [dict(r) for r in rows.mappings()]


async def _shareholder_slips(conn: AsyncConnection, tenant_id: UUID, first: dt.date) -> list[dict[str, Any]]:
    """Gesellschafter-Geschäftsführer mit hinterlegtem Gehalt (Stammdaten → Gehälter)."""
    from app.services.team import tax_profile, to_profile

    out = []
    rows = await conn.execute(text(
        "SELECT s.id, s.name, s.iban, c.gross_month, c.salaries_per_year, c.sv_status, c.tax_class, c.church_tax, c.childless "
        "FROM shareholders s JOIN LATERAL (SELECT * FROM md_compensations m WHERE m.shareholder_id = s.id AND m.valid_from <= :d "
        "ORDER BY m.valid_from DESC LIMIT 1) c ON true WHERE s.tenant_id = :t AND c.gross_month > 0 ORDER BY s.name"),
        {"t": tenant_id, "d": first})
    for r in rows.mappings():
        prof = await tax_profile(conn, r["id"], first)
        profile = to_profile(prof) if prof else TaxProfile(tax_class=r["tax_class"], church_tax=r["church_tax"],
                                                           children_under_25=0 if r["childless"] else 1)
        sv_free = r["sv_status"] == "frei"
        p = payslip(D(r["gross_month"]), "regular", profile=profile, salaries_per_year=r["salaries_per_year"], sv_free=sv_free)
        notes = list(p.notes) + (["SV-frei (beherrschende/r Gesellschafter-Geschäftsführer/in)"] if r["sv_status"] == "frei" else [])
        out.append({"shareholder_id": r["id"], "employee_id": None, "name": r["name"], "employment_type": "shareholder",
                    "hours": None, "iban": r["iban"], **_slip_row(p),
                    "details": {"notes": notes, "tax_class": profile.tax_class, "church_denomination": "ev" if profile.church_tax else None,
                                "pkv_premium": str(p.pkv_premium), "tax_gross": str(p.gross), "sv_days": 0 if sv_free else 30,
                                "tax_days": (first.replace(day=calendar.monthrange(first.year, first.month)[1]) - first).days + 1,
                                "contribution_group": "0000" if sv_free else "1111", "child_allowances": str(profile.child_allowances),
                                "sv_gross_rv": "0" if sv_free else str(min(p.gross, PARAMS_2026.bbg_rv_month)),
                                "sv_gross_kv": "0" if sv_free else str(min(p.gross, PARAMS_2026.bbg_kv_month)),
                                "lines": [{"label": "Geschäftsführergehalt", "quantity": None, "unit": None, "rate": str(r["gross_month"]),
                                           "amount": str(p.gross), "kind": "laufend"}]}})
    return out


ITEM_LABELS = {"overtime": "Überstundenvergütung", "bonus": "Prämie/Zulage (laufend)", "one_time": "Einmalzahlung (sonstiger Bezug)",
               "benefit": "Sachbezug/geldwerter Vorteil", "company_car": "Dienstwagen (geldwerter Vorteil)",
               "tax_free": "Steuerfreie Erstattung", "advance": "Vorschuss (Abzug)", "deduction": "Nettoabzug",
               "garnishment": "Pfändung", "sick_pay_end": "Krankengeld-Zeitraum", "parental_leave": "Elternzeit"}


async def _items(conn: AsyncConnection, tenant_id: UUID, period: str) -> dict[UUID, list[dict[str, Any]]]:
    rows = await conn.execute(text("SELECT id, employee_id, kind, label, quantity, rate, amount, details FROM payroll_items "
                                   "WHERE tenant_id = :t AND period = :p ORDER BY created_at"), {"t": tenant_id, "p": period})
    out: dict[UUID, list[dict[str, Any]]] = {}
    for r in rows.mappings():
        out.setdefault(r["employee_id"], []).append(dict(r))
    return out


async def _year_context(conn: AsyncConnection, tenant_id: UUID, first: dt.date) -> dict[UUID, dict[str, Any]]:
    """Werte der Vormonate desselben Jahres (freigegebene Läufe): sonstige Bezüge, SV-Bemessung, laufender Lohn."""
    rows = await conn.execute(text(
        "SELECT p.employee_id, p.gross, p.details FROM payslips p JOIN payroll_runs r ON r.id = p.run_id WHERE p.tenant_id = :t "
        "AND r.status = 'approved' AND r.period >= :y0 AND r.period < :p AND p.employee_id IS NOT NULL"),
        {"t": tenant_id, "y0": f"{first.year}-01", "p": f"{first:%Y-%m}"})
    ctx: dict[UUID, dict[str, Any]] = {}
    for r in rows.mappings():
        c = ctx.setdefault(r["employee_id"], {"one_time": D(0), "sv_rv": D(0), "sv_kv": D(0), "regular": D(0), "months": 0})
        d = r["details"] or {}
        one = D(str(d.get("one_time") or 0))
        c["one_time"] += one
        c["regular"] += D(r["gross"]) - one
        c["sv_rv"] += D(str(d.get("sv_gross_rv") or 0))
        c["sv_kv"] += D(str(d.get("sv_gross_kv") or 0))
        c["months"] += 1
    return ctx


async def _garnishments(conn: AsyncConnection, tenant_id: UUID) -> dict[UUID, list[dict[str, Any]]]:
    rows = await conn.execute(text(
        "SELECT g.*, coalesce((SELECT sum(CAST(x->>'amount' AS numeric)) FROM payslips p JOIN payroll_runs r ON r.id = p.run_id, "
        "jsonb_array_elements(coalesce(p.details->'garnishments', '[]')) x WHERE r.status = 'approved' AND x->>'id' = g.id::text), 0) "
        "AS paid FROM garnishments g WHERE g.tenant_id = :t AND g.active ORDER BY g.served_on"), {"t": tenant_id})
    out: dict[UUID, list[dict[str, Any]]] = {}
    for r in rows.mappings():
        out.setdefault(r["employee_id"], []).append(dict(r))
    return out


async def _garnish_params(conn: AsyncConnection, tenant_id: UUID) -> Any:
    from app.domain.wage_types import GarnishmentParams

    row = (await conn.execute(text("SELECT garnishment_base, garnishment_first, garnishment_more, garnishment_cap FROM payroll_settings "
                                   "WHERE tenant_id = :t"), {"t": tenant_id})).one_or_none()
    if row is None or row[0] is None:
        return GarnishmentParams()
    return GarnishmentParams(D(row[0]), D(row[1]), D(row[2]), D(row[3]))


async def calculate(conn: AsyncConnection, tenant_id: UUID, period: str, hours: dict[UUID, D] | None = None) -> list[dict[str, Any]]:
    from app.domain.wage_types import absence_effect, company_car_benefit, garnishable
    from app.services.attendance import month_hours

    first, last = period_bounds(period)
    hours = hours or {}
    clocked = await month_hours(conn, tenant_id, period)
    items = await _items(conn, tenant_id, period)
    year_ctx = await _year_context(conn, tenant_id, first)
    garn = await _garnishments(conn, tenant_id)
    gparams = await _garnish_params(conn, tenant_id)
    absences = (await conn.execute(text(
        "SELECT employee_id, kind, start_date, end_date FROM absences WHERE tenant_id = :t AND status = 'approved' "
        "AND kind IN ('sick', 'unpaid', 'parental', 'maternity') AND end_date >= :lo AND start_date <= :hi"),
        {"t": tenant_id, "lo": first - dt.timedelta(days=60), "hi": last})).mappings().all()
    month_days = (last - first).days + 1
    slips: list[dict[str, Any]] = []
    for e in await _active_employees(conn, tenant_id, first, last):
        name = f"{e['first_name']} {e['last_name']}"
        gross, h, notes = _employee_gross(e, first, last, hours.get(e["id"]), clocked.get(e["id"]))
        eff = absence_effect([dict(a) for a in absences if a["employee_id"] == e["id"]], first, last)
        notes += list(eff.notes)
        lines_extra: list[dict[str, Any]] = []
        maternity_top_up = D(0)
        if eff.unpaid_days and e["salary_type"] == "monthly":
            per_day = (D(e["gross_month"]) / 30).quantize(D("0.01"))
            reduction = min(gross, per_day * min(30, eff.unpaid_days))
            gross -= reduction
            lines_extra.append({"label": f"Abzug {eff.unpaid_days} Kalendertage ohne Entgelt (30-Tage-Methode)",
                                "quantity": str(eff.unpaid_days),
                                "unit": "Tage", "rate": str(-per_day), "amount": str(-reduction), "kind": "laufend"})
        regular_extra = benefits = one_time = tax_free = deductions = D(0)
        for it in items.get(e["id"], []):
            amt = D(it["amount"])
            line = {"label": it["label"], "quantity": str(it["quantity"]) if it["quantity"] is not None else None, "unit": "Std."
                    if it["kind"] == "overtime" else None, "rate": str(it["rate"]) if it["rate"] is not None else None, "amount": str(amt),
                    "kind": "einmalig" if it["kind"] == "one_time" else "laufend", "type": it["kind"]}
            if it["kind"] in ("overtime", "bonus"):
                regular_extra += amt
            elif it["kind"] == "one_time":
                one_time += amt
            elif it["kind"] == "benefit":
                benefits += amt
            elif it["kind"] == "tax_free":
                tax_free += amt
            elif it["kind"] in ("advance", "deduction"):
                deductions += amt
            lines_extra.append(line)
        if e.get("car_list_price"):
            car = company_car_benefit(D(e["car_list_price"]), D(e.get("car_distance_km") or 0), e.get("car_kind") or "combustion",
                                      D(e.get("car_copayment") or 0))
            if car:
                benefits += car
                lines_extra.append({"label": "Dienstwagen (geldwerter Vorteil, Listenpreismethode)", "quantity": None, "unit": None,
                                    "rate": None, "amount": str(car), "kind": "laufend", "type": "company_car"})
        ctx = year_ctx.get(e["id"], {"one_time": D(0), "sv_rv": D(0), "sv_kv": D(0), "regular": D(0), "months": 0})
        regular = gross + regular_extra + benefits
        months_left = 12 - first.month + 1
        annual_regular = ctx["regular"] + regular * months_left
        months_employed = ctx["months"] + 1
        sv_room = (max(D(0), PARAMS_2026.bbg_rv_month * months_employed - ctx["sv_rv"] - min(regular, PARAMS_2026.bbg_rv_month)),
                   max(D(0), PARAMS_2026.bbg_kv_month * months_employed - ctx["sv_kv"] - min(regular, PARAMS_2026.bbg_kv_month)))
        kwargs = {"profile": employee_profile(e), "salaries_per_year": e["salaries_per_year"], "rv_exempt": e["rv_exempt"],
                  "one_time": one_time, "benefits": benefits, "annual_regular": annual_regular, "prior_one_time": ctx["one_time"],
                  "sv_room": sv_room}
        try:
            p = payslip(gross + regular_extra, e["employment_type"], tax_free=tax_free, deductions=deductions, **kwargs)
            if eff.maternity_days:
                net_day = max(D(0), (p.net - p.benefits) / 30)
                maternity_top_up = max(D(0), (net_day - 13) * eff.maternity_days).quantize(D("0.01"))
                if maternity_top_up:
                    tax_free += maternity_top_up
                    lines_extra.append({"label": "Zuschuss zum Mutterschaftsgeld (§ 20 MuSchG, steuer-/SV-frei, U2-erstattungsfähig)",
                                        "quantity": str(eff.maternity_days), "unit": "Tage", "rate": None, "amount": str(maternity_top_up),
                                        "kind": "laufend", "type": "tax_free"})
                    p = payslip(gross + regular_extra, e["employment_type"], tax_free=tax_free, deductions=deductions, **kwargs)
            garn_rows: list[dict[str, Any]] = []
            free_net = p.net - p.benefits - sum((D(it["amount"]) / 2 for it in items.get(e["id"], []) if it["kind"] == "overtime"), D(0))
            available = garnishable(free_net, garn[e["id"]][0]["dependents"], gparams) if garn.get(e["id"]) else D(0)
            for g in garn.get(e["id"], []):
                open_claim = D(g["claim_total"]) - D(g["paid"])
                want = D(g["fixed_amount"]) if g["kind"] == "fixed" and g["fixed_amount"] else available
                amount = max(D(0), min(want, open_claim, available))
                if amount:
                    available -= amount
                    garn_rows.append({"id": str(g["id"]), "creditor": g["creditor"], "reference": g["reference"], "amount": str(amount),
                                      "iban": g["creditor_iban"], "remaining": str(open_claim - amount)})
                    lines_extra.append({"label": f"Pfändung {g['creditor']} ({g['reference']})", "quantity": None, "unit": None,
                                        "rate": None,
                                        "amount": str(-amount), "kind": "netto", "type": "garnishment"})
            if garn_rows:
                deductions += sum((D(x["amount"]) for x in garn_rows), D(0))
                p = payslip(gross + regular_extra, e["employment_type"], tax_free=tax_free, deductions=deductions, **kwargs)
        except ValueError as exc:
            raise ConflictError(f"{name}: {exc}") from exc
        legal = legal_details(e, gross, h, first, last)
        legal["lines"] = legal["lines"] + lines_extra
        legal["sv_gross_rv"] = str(p.sv_base_rv)
        legal["sv_gross_kv"] = str(p.sv_base_kv)
        legal["tax_gross"] = str(D(0) if e["employment_type"] == "minijob" else p.gross)
        if eff.sv_lost_days:
            legal["sv_days"] = max(0, legal["sv_days"] - eff.sv_lost_days)
        slips.append({"employee_id": e["id"], "shareholder_id": None, "name": name,
                      "employment_type": e["employment_type"], "hours": h, "iban": e["iban"], **_slip_row(p),
                      "details": {"notes": notes + list(p.notes), "tax_class": e["tax_class"], "number": e["number"],
                                  "church_denomination": e["church_denomination"] if e["church_tax"] else None,
                                  "pkv_premium": str(p.pkv_premium), "tax_id": e["tax_id"], "position": e["position"],
                                  "health_insurer": e["health_insurer"], **legal, "one_time": str(p.one_time), "benefits": str(p.benefits),
                                  "tax_free": str(p.tax_free), "deductions": str(p.deductions), "garnishments": garn_rows,
                                  "sv_reports": [list(r) for r in eff.reports], "month_days": month_days}})
    slips += await _shareholder_slips(conn, tenant_id, first)
    return slips


async def get_run(conn: AsyncConnection, tenant_id: UUID, period: str) -> dict[str, Any] | None:
    run = (await conn.execute(text(
        "SELECT id, period, status, journal_entry_id, salaries_paid_on, taxes_paid_on, sv_paid_on, approved_by, approved_at, "
        "created_by, created_at FROM payroll_runs WHERE tenant_id = :t AND period = :p"),
        {"t": tenant_id, "p": period})).mappings().one_or_none()
    if run is None:
        return None
    slips = [dict(r) for r in (await conn.execute(text(
        f"SELECT {SLIP_COLUMNS} FROM payslips WHERE run_id = :r ORDER BY employment_type = 'shareholder', name"),
        {"r": run["id"]})).mappings()]
    return dict(run) | {"payslips": slips, "totals": totals(slips)}


async def _require(conn: AsyncConnection, tenant_id: UUID, period: str) -> dict[str, Any]:
    run = await get_run(conn, tenant_id, period)
    if run is None:
        raise NotFoundError("Lohnlauf nicht gefunden")
    return run


def totals(slips: list[dict[str, Any]]) -> dict[str, Any]:
    def s(*keys: str) -> D:
        return sum((D(x[k]) for x in slips for k in keys), ZERO)
    return {
        "count": len(slips), "gross": s("gross"), "net": s("net"), "payout": s("payout"),
        "lst": s("lst"), "solz": s("solz"), "church": s("church"), "taxes": s("lst", "solz", "church"),
        "sv_ee": s("rv_ee", "av_ee", "kv_ee", "pv_ee"), "sv_er": s("rv_er", "av_er", "kv_er", "pv_er"),
        "levies": s("levies"), "flat_tax": s("flat_tax"), "pkv_subsidy": s("pkv_subsidy"),
        "sv_total": s("rv_ee", "av_ee", "kv_ee", "pv_ee", "rv_er", "av_er", "kv_er", "pv_er", "levies", "flat_tax"),
        "employer_cost": s("employer_cost"),
    }


async def list_runs(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT r.id, r.period, r.status, r.salaries_paid_on, r.taxes_paid_on, r.sv_paid_on, count(p.id) AS count, "
        "COALESCE(sum(p.gross), 0) AS gross, COALESCE(sum(p.payout), 0) AS payout, COALESCE(sum(p.employer_cost), 0) AS employer_cost "
        "FROM payroll_runs r LEFT JOIN payslips p ON p.run_id = r.id WHERE r.tenant_id = :t GROUP BY r.id ORDER BY r.period DESC"),
        {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def save_run(conn: AsyncConnection, tenant_id: UUID, period: str, actor: str, hours: dict[UUID, D] | None = None) -> dict[str, Any]:
    """Legt den Lohnlauf als Entwurf an oder berechnet ihn neu (nur solange er nicht freigegeben ist)."""
    first, _ = period_bounds(period)
    existing = await get_run(conn, tenant_id, period)
    if existing and existing["status"] != "draft":
        raise ConflictError("Der Lohnlauf ist freigegeben und kann nicht mehr geändert werden")
    if existing and hours is None:  # Stunden aus der bisherigen Berechnung übernehmen
        hours = {p["employee_id"]: D(p["hours"]) for p in existing["payslips"] if p["employee_id"] and p["hours"] is not None}
    slips = await calculate(conn, tenant_id, period, hours)
    if not slips:
        raise ConflictError("Für diesen Monat gibt es keine Mitarbeiter oder Geschäftsführer-Gehälter")
    if existing:
        run_id = existing["id"]
        await conn.execute(text("DELETE FROM payslips WHERE run_id = :r"), {"r": run_id})
    else:
        run_id = await conn.scalar(text("INSERT INTO payroll_runs (tenant_id, period, created_by) VALUES (:t, :p, :a) RETURNING id"),
                                   {"t": tenant_id, "p": period, "a": actor})
    for s in slips:
        await conn.execute(text(
            "INSERT INTO payslips (run_id, tenant_id, employee_id, shareholder_id, name, employment_type, hours, gross, lst, solz, church, "
            "rv_ee, av_ee, kv_ee, pv_ee, rv_er, av_er, kv_er, pv_er, levies, flat_tax, pkv_subsidy, net, payout, employer_cost, iban, "
            "details) VALUES (:r, :t, :employee_id, :shareholder_id, :name, :employment_type, :hours, :gross, :lst, :solz, :church, "
            ":rv_ee, :av_ee, :kv_ee, :pv_ee, :rv_er, :av_er, :kv_er, :pv_er, :levies, :flat_tax, :pkv_subsidy, :net, :payout, "
            ":employer_cost, :iban, CAST(:details AS jsonb))"),
            dict(s) | {"r": run_id, "t": tenant_id, "details": json.dumps(s["details"], default=str)})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="payroll.calculated", entity_type="payroll_run",
                       entity_id=run_id, after={"period": period, "payslips": len(slips), "first": str(first)})
    return await _require(conn, tenant_id, period)


async def delete_run(conn: AsyncConnection, tenant_id: UUID, period: str, actor: str) -> None:
    run = await get_run(conn, tenant_id, period)
    if run is None:
        raise NotFoundError("Lohnlauf nicht gefunden")
    if run["status"] != "draft":
        raise ConflictError("Freigegebene Lohnläufe können nicht gelöscht werden")
    await conn.execute(text("DELETE FROM payroll_runs WHERE id = :r"), {"r": run["id"]})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="payroll.deleted", entity_type="payroll_run",
                       entity_id=run["id"], after={"period": period})


def journal_lines(chart: str, slips: list[dict[str, Any]]) -> list[Any]:
    """Lohnbuchung: Aufwand an Verbindlichkeiten (Lohn, Lohnsteuer, Sozialversicherung)."""
    from app.services.accounting import Line, acc

    t = totals(slips)
    mini = [s for s in slips if s["employment_type"] == "minijob"]
    other = [s for s in slips if s["employment_type"] != "minijob"]
    gross_regular = sum((D(s["gross"]) for s in other), ZERO)
    gross_mini = sum((D(s["gross"]) for s in mini), ZERO)
    social = t["sv_er"] + t["levies"] + t["pkv_subsidy"]

    def det(key: str) -> D:
        return sum((D(str((s.get("details") or {}).get(key) or 0)) for s in slips), ZERO)

    benefits, tax_free = det("benefits"), det("tax_free")
    garnished = sum((D(g["amount"]) for s in slips for g in (s.get("details") or {}).get("garnishments", [])), ZERO)
    advances = det("deductions") - garnished
    lines = [
        Line(acc(chart, "salaries"), debit=gross_regular, note="Bruttolöhne und -gehälter"),
        Line(acc(chart, "salaries_minijob"), debit=gross_mini, note="Minijobs"),
        Line(acc(chart, "social_security"), debit=social, note="Arbeitgeberanteile, Umlagen, PKV-Zuschüsse"),
        Line(acc(chart, "flat_wage_tax"), debit=t["flat_tax"], note="Pauschalsteuer Minijobs"),
        Line(acc(chart, "wages_payable"), credit=t["payout"], note="Auszahlungsbeträge"),
        Line(acc(chart, "wage_tax_payable"), credit=t["taxes"], note="Lohnsteuer, Soli, Kirchensteuer"),
        Line(acc(chart, "social_security_payable"), credit=t["sv_total"], note="Sozialversicherung und Minijob-Zentrale"),
        Line(acc(chart, "tax_free_benefits"), debit=tax_free, note="Steuerfreie Zahlungen (Erstattungen, Zuschuss Mutterschaftsgeld)"),
        Line(acc(chart, "benefits_offset"), credit=benefits, note="Sachbezüge/geldwerte Vorteile (USt auf Kfz-Gestellung prüfen)"),
        Line(acc(chart, "employee_receivables"), credit=advances, note="Verrechnete Vorschüsse und Nettoabzüge"),
        Line(acc(chart, "other_liabilities"), credit=garnished, note="Einbehaltene Pfändungsbeträge (an Gläubiger)"),
    ]
    return [ln for ln in lines if ln.debit or ln.credit]


async def approve_run(conn: AsyncConnection, ledger: Any, tenant_id: UUID, period: str, actor: str) -> dict[str, Any]:
    from app.services.accounting import chart_of, post

    run = await get_run(conn, tenant_id, period)
    if run is None:
        raise NotFoundError("Lohnlauf nicht gefunden")
    if run["status"] != "draft":
        raise ConflictError("Lohnlauf ist bereits freigegeben")
    missing = [p["name"] for p in run["payslips"] if p["employment_type"] != "shareholder" and p["hours"] is not None
               and D(p["hours"]) == 0]
    if missing:
        raise ConflictError(f"Stunden fehlen für: {', '.join(missing)}")
    chart = await chart_of(conn, tenant_id)
    _, last = period_bounds(period)
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=last, description=f"Lohn und Gehalt {last:%m/%Y}",
                       document_ref=f"LOHN-{period}", source_type="payroll", source_id=run["id"],
                       lines=journal_lines(chart, run["payslips"]), method="rule", actor=actor)
    # Status zuletzt setzen: danach greift der Unveränderlichkeits-Trigger auf den Abrechnungen
    await conn.execute(text("UPDATE payroll_runs SET status = 'approved', journal_entry_id = :e, approved_by = :a, approved_at = now() "
                            "WHERE id = :r"), {"e": entry, "a": actor, "r": run["id"]})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="payroll.approved", entity_type="payroll_run",
                       entity_id=run["id"], after={"period": period, "journal_entry_id": str(entry)})
    return await _require(conn, tenant_id, period)


PAYMENTS = {
    "salaries": ("wages_payable", "payout", "salaries_paid_on", "Nettolöhne"),
    "taxes": ("wage_tax_payable", "taxes", "taxes_paid_on", "Lohnsteuer an das Finanzamt"),
    "sv": ("social_security_payable", "sv_total", "sv_paid_on", "Sozialversicherungsbeiträge"),
}


async def mark_paid(conn: AsyncConnection, ledger: Any, tenant_id: UUID, period: str, what: str, paid_on: dt.date,
                    actor: str) -> dict[str, Any]:
    from app.services.accounting import Line, acc, chart_of, post

    if what not in PAYMENTS:
        raise ConflictError("Unbekannte Zahlung")
    run = await get_run(conn, tenant_id, period)
    if run is None or run["status"] != "approved":
        raise ConflictError("Bitte den Lohnlauf zuerst freigeben")
    role, total_key, column, label = PAYMENTS[what]
    if run[column]:
        raise ConflictError(f"{label} sind bereits als bezahlt gebucht")
    amount = run["totals"][total_key]
    # Mit den Nettolöhnen werden auch einbehaltene Pfändungsbeträge an die Gläubiger überwiesen (gleiche SEPA-Datei)
    garnished = sum((D(g["amount"]) for p in run["payslips"] for g in (p.get("details") or {}).get("garnishments", [])), ZERO) \
        if what == "salaries" else ZERO
    if amount > 0 or garnished > 0:
        chart = await chart_of(conn, tenant_id)
        lines = [Line(acc(chart, role), debit=amount), Line(acc(chart, "other_liabilities"), debit=garnished),
                 Line(acc(chart, "bank"), credit=amount + garnished)]
        await post(conn, ledger, tenant_id, chart=chart, entry_date=paid_on, description=f"{label} {period}",
                   document_ref=f"LOHN-{period}", source_type="payroll_payment",
                   source_id=uuid.uuid5(PAYROLL_NS, f"{run['id']}:{what}"),
                   lines=[ln for ln in lines if ln.debit or ln.credit], method="rule", actor=actor)
    await conn.execute(text(f"UPDATE payroll_runs SET {column} = :d WHERE id = :r"), {"d": paid_on, "r": run["id"]})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"payroll.paid.{what}", entity_type="payroll_run",
                       entity_id=run["id"], after={"period": period, "amount": str(amount), "paid_on": str(paid_on)})
    return await _require(conn, tenant_id, period)


def sepa_transfers(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Überweisungen der Nettolöhne (für SEPA pain.001)."""
    out = []
    for p in run["payslips"]:
        if D(p["payout"]) <= 0:
            continue
        if not p["iban"]:
            raise ConflictError(f"IBAN fehlt für {p['name']}")
        out.append({"name": p["name"], "iban": p["iban"], "amount": D(p["payout"]),
                    "reference": f"Lohn/Gehalt {run['period'][5:]}/{run['period'][:4]}",
                    "end_to_end": f"LOHN-{run['period']}-{str(p['id'])[:8]}"})
        for g in (p.get("details") or {}).get("garnishments", []):
            if g.get("iban") and D(g["amount"]) > 0:
                out.append({"name": g["creditor"][:70], "iban": g["iban"], "amount": D(g["amount"]),
                            "reference": f"Pfaendung {g['reference']} {p['name']} {run['period']}"[:140],
                            "end_to_end": f"PF-{run['period']}-{g['id'][:8]}"})
    return out


# ───────────────────────── Lohnsteuer-Anmeldung ─────────────────────────

def lsta_values(run: dict[str, Any]) -> dict[str, Any]:
    slips = run["payslips"]
    church_ev = sum((D(p["church"]) for p in slips if (p["details"] or {}).get("church_denomination") == "ev"), ZERO)
    church_rk = sum((D(p["church"]) for p in slips if (p["details"] or {}).get("church_denomination") != "ev"), ZERO)
    t = run["totals"]
    return {"employees": sum(1 for p in slips if p["employment_type"] != "minijob"), "lst": t["lst"], "solz": t["solz"],
            "church_ev": church_ev, "church_rk": church_rk, "flat_lst": ZERO,
            "total": t["lst"] + t["solz"] + church_ev + church_rk}


async def lsta_xml(conn: AsyncConnection, tenant_id: UUID, period: str, *, test: bool) -> tuple[str, dict[str, Any]]:
    from app.core.config import get_settings
    from app.domain.elster import ElsterError
    from app.domain.elster import lsta_xml as build
    from app.services.invoices import today_berlin
    from app.services.masterdata import current_company_profile

    run = await get_run(conn, tenant_id, period)
    if run is None or run["status"] != "approved":
        raise ConflictError("Die Lohnsteuer-Anmeldung setzt einen freigegebenen Lohnlauf voraus")
    s = get_settings()
    profile = await current_company_profile(conn, tenant_id, today_berlin())
    if profile is None or not profile["tax_number"]:
        raise ConflictError("Steuernummer im Firmenprofil fehlt")
    v = lsta_values(run)
    try:
        xml = build(period=period, tax_number=profile["tax_number"], state=s.company_state,
                    company={"name": profile["legal_name"], "street": profile["street"], "postal_code": profile["postal_code"],
                             "city": profile["city"]}, employees=v["employees"], lst=v["lst"], flat_lst=v["flat_lst"], solz=v["solz"],
                    church_ev=v["church_ev"], church_rk=v["church_rk"], hersteller_id=s.elster_hersteller_id or "00000", test=test,
                    created=today_berlin())
    except ElsterError as exc:
        raise ConflictError(str(exc)) from exc
    return xml, v


async def lsta_validate(conn: AsyncConnection, tenant_id: UUID, period: str, actor: str) -> dict[str, Any]:
    from app.services.elster import _client, _record

    xml, _ = await lsta_xml(conn, tenant_id, period, test=True)
    result = await _client().validate(xml, f"LStA_{period[:4]}")
    await _record(conn, tenant_id, period, True, "validated" if result.ok else "error", xml, actor, response=result.response,
                  error=result.error, kind="lsta")
    return {"ok": result.ok, "error": result.error}


async def lsta_send(conn: AsyncConnection, tenant_id: UUID, period: str, pin: str, actor: str) -> dict[str, Any]:
    from pathlib import Path

    from app.core.config import get_settings
    from app.services.elster import _client, _record

    s = get_settings()
    if not s.elster_hersteller_id:
        raise ConflictError("ELSTER_HERSTELLER_ID fehlt (Registrierung im ELSTER-Entwicklerbereich)")
    if not Path(s.elster_cert_path).exists():
        raise ConflictError("ELSTER-Zertifikat (.pfx) nicht gefunden")
    xml, _ = await lsta_xml(conn, tenant_id, period, test=s.elster_test_mode)
    result = await _client().send(xml, f"LStA_{period[:4]}", s.elster_cert_path, pin)
    await _record(conn, tenant_id, period, s.elster_test_mode, "sent" if result.ok else "error", xml, actor,
                  ticket=result.transfer_ticket, response=result.server_response or result.response, error=result.error, kind="lsta")
    return {"ok": result.ok, "transfer_ticket": result.transfer_ticket, "error": result.error, "test_mode": s.elster_test_mode}


# ───────────────────────── Abrechnung als PDF ─────────────────────────

async def get_payslip(conn: AsyncConnection, tenant_id: UUID, payslip_id: UUID, *, employee_id: UUID | None = None,
                      approved_only: bool = False) -> dict[str, Any]:
    row = (await conn.execute(text(
        f"SELECT {', '.join('p.' + c.strip() for c in SLIP_COLUMNS.split(','))}, r.period, r.status FROM payslips p "
        "JOIN payroll_runs r ON r.id = p.run_id WHERE p.tenant_id = :t AND p.id = :id"),
        {"t": tenant_id, "id": payslip_id})).mappings().one_or_none()
    if row is None or (employee_id is not None and row["employee_id"] != employee_id) or (approved_only and row["status"] != "approved"):
        raise NotFoundError("Abrechnung nicht gefunden")
    return dict(row)


def _eur(v: Any) -> str:
    return f"{D(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


PAYSLIP_CSS = """
@page { size: A4; margin: 12mm 15mm 14mm;
  @bottom-center { content: "Seite " counter(page) " von " counter(pages); font-family: 'DejaVu Sans', sans-serif; font-size: 7pt;
                   color: #6b6b7b; } }
body { font-family: 'DejaVu Sans', sans-serif; font-size: 8.4pt; color: #1e1e28; }
h1 { font-size: 14pt; margin: 0 0 1mm; } h2 { font-size: 9.2pt; margin: 3.6mm 0 1.2mm; color: #0b6b65; break-after: avoid; }
table, .cols > div { break-inside: avoid; }
.head { display: flex; justify-content: space-between; margin-bottom: 3mm; }
.muted { color: #6b6b7b; font-size: 7.8pt; font-weight: normal; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 0.8mm 0.8mm; border-bottom: 0.3pt solid #e4e4ea; vertical-align: top; }
th { text-align: right; font-weight: normal; color: #6b6b7b; font-size: 7.6pt; } th:first-child, .l { text-align: left; }
.r { text-align: right; white-space: nowrap; } .c { text-align: center; color: #6b6b7b; }
.total td { font-weight: bold; border-top: 0.8pt solid #1e1e28; border-bottom: 0; }
.meta td:first-child { color: #6b6b7b; width: 48%; }
.cols { display: flex; gap: 7mm; } .cols > div { flex: 1; min-width: 0; }
.grid th, .grid td { font-size: 7.6pt; } .grid th:first-child { text-align: right; }
.draft { color: #c0262d; font-weight: bold; margin-bottom: 3mm; }
ul { padding-left: 4mm; margin: 0; color: #4b4b5b; font-size: 7.8pt; }
"""


def payslip_html(slip: dict[str, Any], company: dict[str, Any] | None, employee: dict[str, Any] | None,
                 ytd: dict[str, Any] | None = None, vacation: dict[str, Any] | None = None) -> str:
    """Entgeltbescheinigung nach § 108 Abs. 3 Satz 1 GewO und Entgeltbescheinigungsverordnung (EBV).

    Pflichtangaben: Arbeitgeber (Name, Anschrift), Beschäftigte/r (Name, Anschrift, Geburtsdatum), Versicherungsnummer,
    Beschäftigungsbeginn/-ende, Abrechnungszeitraum, Steuer-ID, Steuerklasse, Kinderfreibeträge, Kirchensteuermerkmal,
    Krankenkasse, Beitragsgruppen- und Personengruppenschlüssel, SV-Tage, Zusammensetzung des Entgelts (Art, Menge, Satz,
    Betrag), steuer- und SV-pflichtiges Brutto, Steuer- und SV-Abzüge einzeln, Netto, Be-/Abzüge, Auszahlungsbetrag.
    """
    from html import escape

    first, last = period_bounds(slip["period"])
    d = slip["details"] or {}
    emp = employee or {}

    def g(key: str, fallback_key: str | None = None) -> Any:
        v = d.get(key)
        return v if v not in (None, "") else emp.get(fallback_key or key)

    def date(v: Any) -> str:
        if not v:
            return "–"
        v = dt.date.fromisoformat(v) if isinstance(v, str) else v
        return f"{v:%d.%m.%Y}"

    def num(v: Any, places: int = 2) -> str:
        return f"{D(v):,.{places}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    kinds = {"regular": "sozialversicherungspflichtig", "minijob": "geringfügig entlohnt (Minijob)", "werkstudent": "Werkstudent/in",
             "shareholder": "Gesellschafter-Geschäftsführer/in"}
    confession = {"ev": "ev (evangelisch)", "rk": "rk (römisch-katholisch)", "other": "Kirchensteuerpflicht"}
    church = d.get("church_denomination")
    is_sh = slip["employment_type"] == "shareholder"

    # Kopf
    comp = escape(company["legal_name"]) if company else ""
    comp_addr = escape(f"{company['street']}, {company['postal_code']} {company['city']}") if company else ""
    name = f"{emp['first_name']} {emp['last_name']}" if emp else slip["name"]
    addr = "<br>".join(escape(x) for x in [name, emp.get("street") or "",
                                           f"{emp.get('postal_code') or ''} {emp.get('city') or ''}".strip()] if x)

    person = [("Personalnummer", str(d.get("number") or "–")), ("Geburtsdatum", date(g("birth_date"))),
              ("Versicherungsnummer", g("social_security_number") or ("–" if not is_sh else "entfällt (SV-frei)")),
              ("Eintritt", date(g("entry_date"))), ("Austritt", date(g("exit_date")) if g("exit_date") else "–"),
              ("Beschäftigung", kinds.get(slip["employment_type"], slip["employment_type"]))]
    if d.get("position"):
        person.append(("Tätigkeit", d["position"]))
    tax = [("Steuer-ID", g("tax_id") or "–"), ("Steuerklasse", str(d.get("tax_class") or "–")),
           ("Kinderfreibeträge", num(g("child_allowances") or 0, 1)),
           ("Konfession (KiSt)", confession.get(church, "–") if church else "– (keine)"),
           ("Steuertage", str(d.get("tax_days") or "–"))]
    sv = [("Krankenkasse", d.get("health_insurer") or ("private Krankenversicherung" if d.get("health_insurance") == "pkv" else "–")),
          ("Beitragsgruppe (KV RV AV PV)", " ".join(d.get("contribution_group") or "") or "–"),
          ("Personengruppe", f"{d['person_group']} ({d['person_group_label']})" if d.get("person_group") else "–"),
          ("SV-Tage", str(d.get("sv_days") or "–"))]

    def meta(rows: list[tuple[str, str]]) -> str:
        return "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rows)

    # Bezüge (§ 108 GewO: Art, Menge, Satz, Betrag)
    lines = d.get("lines") or [{"label": "Bruttoentgelt", "quantity": slip["hours"], "unit": "Std." if slip["hours"] is not None else None,
                                "rate": None, "amount": slip["gross"], "kind": "laufend"}]
    def pay_row(ln: dict[str, Any]) -> str:
        has_qty = ln.get("quantity") is not None
        qty = f"{num(ln['quantity'])} {escape(ln.get('unit') or '')}" if has_qty else ""
        rate = _eur(ln["rate"]) if ln.get("rate") and has_qty else ""
        flag = "L" if ln.get("kind") == "laufend" else "E"
        return (f"<tr><td>{escape(ln['label'])}</td><td class='r'>{qty}</td><td class='r'>{rate}</td>"
                f"<td class='c'>{flag}</td><td class='r'>{_eur(ln['amount'])}</td></tr>")

    pay_rows = "".join(pay_row(ln) for ln in lines)

    if d.get("tax_gross") is not None:
        tax_gross = D(str(d["tax_gross"]))
    else:
        tax_gross = D(0) if slip["employment_type"] == "minijob" else D(slip["gross"])
    sv_rv = D(d.get("sv_gross_rv") or 0)
    sv_kv = D(d.get("sv_gross_kv") or 0)
    brutto = [("Gesamtbrutto", slip["gross"]), ("Steuerrechtliches Brutto (laufend)", tax_gross),
              ("SV-Brutto RV/AV", sv_rv), ("SV-Brutto KV/PV", sv_kv)]
    tax_rows = [("Lohnsteuer", slip["lst"]), ("Solidaritätszuschlag", slip["solz"]), ("Kirchensteuer", slip["church"])]
    sv_rows = [("Krankenversicherung", slip["kv_ee"]), ("Rentenversicherung", slip["rv_ee"]),
               ("Arbeitslosenversicherung", slip["av_ee"]), ("Pflegeversicherung", slip["pv_ee"])]
    sum_tax = sum((D(v) for _, v in tax_rows), D(0))
    sum_sv = sum((D(v) for _, v in sv_rows), D(0))

    def rows(items: list[tuple[str, Any]], *, all_rows: bool = True) -> str:
        return "".join(f"<tr><td>{escape(n)}</td><td class='r'>{_eur(v)}</td></tr>" for n, v in items if all_rows or D(v))

    rows_er = [("Krankenversicherung", slip["kv_er"]), ("Rentenversicherung", slip["rv_er"]),
               ("Arbeitslosenversicherung", slip["av_er"]), ("Pflegeversicherung", slip["pv_er"]),
               ("Umlagen U1/U2/Insolvenzgeld", slip["levies"]), ("Pauschalsteuer (Minijob)", slip["flat_tax"]),
               ("Zuschuss private KV/PV", slip["pkv_subsidy"])]
    pkv = D(d.get("pkv_premium") or 0)
    subsidy = D(slip["pkv_subsidy"])
    adjust = ""
    if subsidy:
        adjust += ("<tr><td>+ Arbeitgeberzuschuss private KV/PV (§ 257 SGB V, § 61 SGB XI) – steuerfrei</td>"
                   f"<td class='r'>{_eur(subsidy)}</td></tr>")
    pkv_note = (f"<div class='muted'>Ihren Beitrag zur privaten KV/PV ({_eur(pkv)}) zahlen Sie selbst an Ihre Versicherung.</div>"
                if pkv else "")

    midi = d.get("midijob")
    midi_html = (f"<div class='muted' style='margin-top:1mm'>Übergangsbereich § 20a SGB IV: beitragspflichtige Einnahme "
                 f"{_eur(midi['total_base'])}, AN-Anteil aus {_eur(midi['employee_base'])}, Faktor F {num(midi['factor'], 4)}</div>"
                 if midi else "")
    tax_total = f"<tr class='total'><td>Summe Steuern</td><td class='r'>{_eur(sum_tax)}</td></tr>"
    sv_total = f"<tr class='total'><td>Summe SV</td><td class='r'>{_eur(sum_sv)}</td></tr>"
    notes_html = "".join(f"<li>{escape(n)}</li>" for n in d.get("notes", []) if not (midi and n.startswith("Übergangsbereich")))
    notes_block = f"<h2>Hinweise</h2><ul>{notes_html}</ul>" if notes_html else ""
    ytd_html = ""
    if ytd:
        cells = [("Gesamtbrutto", ytd["gross"]), ("Steuerbrutto", ytd["tax_gross"]), ("Lohnsteuer", ytd["lst"]), ("Soli", ytd["solz"]),
                 ("KiSt", ytd["church"]), ("SV-Abzüge", ytd["sv_ee"]), ("Netto", ytd["net"])]
        ytd_html = (f"<h2>Jahreswerte {first.year} (Januar – {first:%B})</h2><table class='grid'><tr>"
                    + "".join(f"<th>{escape(k)}</th>" for k, _ in cells) + "</tr><tr>"
                    + "".join(f"<td class='r'>{_eur(v)}</td>" for _, v in cells) + "</tr></table>")
    vac_html = ""
    if vacation:
        vac_html = (f"<h2>Urlaub {vacation['year']} <span class='muted'>(Stand {date(vacation['as_of'])})</span></h2>"
                    "<table class='grid'><tr><th>Anspruch</th><th>Übertrag Vorjahr</th><th>genommen</th><th>geplant</th>"
                    "<th>Rest</th></tr>"
                    f"<tr><td class='r'>{num(vacation['entitlement'], 1)}</td><td class='r'>{num(vacation['carryover_effective'], 1)}</td>"
                    f"<td class='r'>{num(vacation['taken'], 1)}</td><td class='r'>{num(vacation['planned'], 1)}</td>"
                    f"<td class='r'><b>{num(vacation['remaining'], 1)}</b></td></tr></table>")

    draft = "<div class='draft'>ENTWURF – nicht freigegeben</div>" if slip["status"] != "approved" else ""
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><style>{PAYSLIP_CSS}</style></head><body>{draft}
<div class="head"><div><div class="muted">{comp} · {comp_addr}</div><div style="margin-top:4mm">{addr}</div></div>
<div style="text-align:right"><h1>Entgeltbescheinigung</h1><div>{first:%B %Y}</div>
<div class="muted">Abrechnungszeitraum {first:%d.%m.%Y} – {last:%d.%m.%Y}</div>
<div class="muted">nach § 108 Abs. 3 Satz 1 GewO / EBV</div></div></div>

<div class="cols">
<div><h2>Persönliche Daten</h2><table class="meta">{meta(person)}</table></div>
<div><h2>Steuer</h2><table class="meta">{meta(tax)}</table><h2>Sozialversicherung</h2><table class="meta">{meta(sv)}</table></div>
</div>

<h2>Bezüge</h2>
<table><tr><th class="l">Lohnart</th><th>Menge</th><th>Satz</th><th class="c">L/E</th><th>Betrag</th></tr>{pay_rows}
<tr class="total"><td colspan="4">Gesamtbrutto</td><td class="r">{_eur(slip['gross'])}</td></tr></table>

<div class="cols">
<div><h2>Steuer- und SV-Brutto</h2><table>{rows(brutto[1:])}</table>{midi_html}</div>
<div><h2>Steuerrechtliche Abzüge</h2><table>{rows(tax_rows)}{tax_total}</table></div>
</div>

<div class="cols">
<div><h2>Sozialversicherung (Arbeitnehmeranteil)</h2><table>{rows(sv_rows)}{sv_total}</table></div>
<div><h2>Auszahlung</h2><table>
<tr><td>Gesamtbrutto</td><td class="r">{_eur(slip['gross'])}</td></tr>
<tr><td>– Steuern</td><td class="r">{_eur(sum_tax)}</td></tr><tr><td>– Sozialversicherung</td><td class="r">{_eur(sum_sv)}</td></tr>
<tr class="total"><td>Nettoentgelt</td><td class="r">{_eur(slip['net'])}</td></tr>{adjust}
<tr class="total"><td>Auszahlungsbetrag</td><td class="r">{_eur(slip['payout'])}</td></tr></table>
<div class="muted" style="margin-top:1mm">Überweisung auf IBAN {escape(_mask(slip['iban']))}</div>{pkv_note}</div>
</div>

{ytd_html}
<div class="cols"><div><h2>Arbeitgeberanteile <span class="muted">(zur Information)</span></h2><table>{rows(rows_er, all_rows=False)}
<tr class="total"><td>Gesamtkosten Arbeitgeber</td><td class="r">{_eur(slip['employer_cost'])}</td></tr></table></div>
<div>{vac_html}{notes_block}</div></div>
<p class="muted" style="margin-top:6mm">L = laufender Bezug, E = Einmalbezug. Berechnung nach den Parametern {first.year}
(Lohnsteuer: vereinfachtes Verfahren, maßgeblich sind ELStAM und die Beitragssätze Ihrer Krankenkasse).
Bitte prüfen Sie Ihre Abrechnung und melden Sie Abweichungen umgehend. Erstellt mit OpenEmployed.</p></body></html>"""


def _mask(iban: str | None) -> str:
    if not iban:
        return "–"
    return f"{iban[:4]} •••• {iban[-4:]}"


async def year_to_date(conn: AsyncConnection, tenant_id: UUID, slip: dict[str, Any]) -> dict[str, Any]:
    """Kumulierte Jahreswerte bis einschließlich dieses Monats (freigegebene Läufe + diese Abrechnung)."""
    who, wid = ("employee_id", slip["employee_id"]) if slip["employee_id"] else ("shareholder_id", slip["shareholder_id"])
    rows = (await conn.execute(text(
        f"SELECT p.id, p.employment_type, p.gross, p.lst, p.solz, p.church, p.rv_ee, p.av_ee, p.kv_ee, p.pv_ee, p.net, p.details "
        f"FROM payslips p JOIN payroll_runs r ON r.id = p.run_id WHERE p.tenant_id = :t AND p.{who} = :w "
        "AND r.period >= :y0 AND r.period <= :p AND (r.status = 'approved' OR p.id = :id)"),
        {"t": tenant_id, "w": wid, "y0": slip["period"][:4] + "-01", "p": slip["period"], "id": slip["id"]})).mappings().all()
    out = {k: D(0) for k in ("gross", "tax_gross", "lst", "solz", "church", "sv_ee", "net")}
    for r in rows:
        det = r["details"] or {}
        out["gross"] += D(r["gross"])
        tg = det.get("tax_gross")
        out["tax_gross"] += D(tg) if tg is not None else (D(0) if r["employment_type"] == "minijob" else D(r["gross"]))
        out["lst"] += D(r["lst"])
        out["solz"] += D(r["solz"])
        out["church"] += D(r["church"])
        out["sv_ee"] += D(r["rv_ee"]) + D(r["av_ee"]) + D(r["kv_ee"]) + D(r["pv_ee"])
        out["net"] += D(r["net"])
    return out


async def payslip_pdf(conn: AsyncConnection, tenant_id: UUID, slip: dict[str, Any]) -> bytes:
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    from app.services.invoices import today_berlin
    from app.services.masterdata import current_company_profile

    company = await current_company_profile(conn, tenant_id, today_berlin())
    employee = await get_employee(conn, tenant_id, slip["employee_id"]) if slip["employee_id"] else None
    ytd = await year_to_date(conn, tenant_id, slip)
    vacation = None
    if employee and employee.get("vacation_days") is not None:
        from app.services.attendance import balance
        from app.services.attendance import employee as att_employee

        try:
            bal = await balance(conn, tenant_id, await att_employee(conn, tenant_id, employee["id"]), int(slip["period"][:4]))
            vacation = {**bal, "as_of": today_berlin()}
        except Exception:  # Urlaubskonto ist eine Zusatzinformation
            vacation = None
    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False, fail_on_errors=False)
    pdf: bytes = HTML(string=payslip_html(slip, dict(company) if company else None, employee, ytd, vacation), url_fetcher=fetcher,
                      base_url=None).write_pdf()
    return pdf


# ───────────────────────── Self-Service (Portal) ─────────────────────────

async def employee_for_email(conn: AsyncConnection, tenant_id: UUID, email: str) -> dict[str, Any] | None:
    row = (await conn.execute(text(
        "SELECT id, number, first_name, last_name FROM employees WHERE tenant_id = :t AND email = :e AND portal_enabled "
        "AND archived_at IS NULL"), {"t": tenant_id, "e": email})).mappings().one_or_none()
    return dict(row) if row else None


async def self_service(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID) -> dict[str, Any]:
    e = await get_employee(conn, tenant_id, employee_id)
    slips = await conn.execute(text(
        "SELECT p.id, r.period, p.gross, p.net, p.payout FROM payslips p JOIN payroll_runs r ON r.id = p.run_id "
        "WHERE p.tenant_id = :t AND p.employee_id = :e AND r.status = 'approved' ORDER BY r.period DESC"),
        {"t": tenant_id, "e": employee_id})
    visible = ("number", "first_name", "last_name", "email", "phone", "street", "postal_code", "city", "position", "entry_date",
               "employment_type", "salary_type", "weekly_hours", "vacation_days", "tax_class", "child_allowances", "church_tax",
               "health_insurance", "health_insurer", "iban", "account_holder")
    profile = {k: e[k] for k in visible}
    profile["iban"] = _mask(e["iban"]) if e["iban"] else None
    return {"profile": profile, "payslips": [dict(r) for r in slips.mappings()],
            "editable": list(SELF_SERVICE_FIELDS)}


async def self_update(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, data: dict[str, Any], email: str) -> None:
    changes = {k: v for k, v in data.items() if k in SELF_SERVICE_FIELDS and v is not None}
    if "iban" in changes:
        changes["iban"] = changes["iban"].replace(" ", "").upper()
    if not changes:
        return
    await conn.execute(text(f"UPDATE employees SET {', '.join(f'{k} = :{k}' for k in changes)}, updated_at = now() "
                            "WHERE tenant_id = :t AND id = :id"), changes | {"t": tenant_id, "id": employee_id})
    await audit.record(conn, tenant_id=tenant_id, actor=f"portal:{email}", action="employee.self_updated", entity_type="employee",
                       entity_id=employee_id, after={k: ("***" if k == "iban" else v) for k, v in changes.items()})
