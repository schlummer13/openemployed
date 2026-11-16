"""Steuerauswertung: Umsatzsteuer-Voranmeldung (Kennzahlen), Jahresübersicht mit Rücklagen, Steuerkalender.

Grundlage: Soll-Versteuerung; Zuordnung über den bei Freigabe/Buchung festgelegten Voranmeldungszeitraum
(``vat_period``). Bemessungsgrundlagen werden – wie im amtlichen Vordruck – auf volle Euro gekürzt; die Steuer zu
Kz 81/86/89 berechnet das Formular aus der Bemessungsgrundlage. ⚠️ Kennzahlen jährlich mit dem Vordruck abgleichen.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.money import ZERO, round_cent
from app.domain.tax_calendar import deadlines
from app.domain.tax_rates import estimate_income_taxes

_SPEC = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2]|Q[1-4])$")

KZ_LABELS: dict[str, str] = {
    "41": "Innergemeinschaftliche Lieferungen an Abnehmer mit USt-IdNr.",
    "21": "Nicht steuerbare sonstige Leistungen (EU, § 18b) – Leistungsempfänger schuldet die Steuer",
    "45": "Übrige nicht steuerbare Umsätze (Leistungsort nicht im Inland)",
    "81": "Steuerpflichtige Umsätze 19 %",
    "86": "Steuerpflichtige Umsätze 7 %",
    "89": "Innergemeinschaftliche Erwerbe 19 %",
    "46": "Leistungen von EU-Unternehmern (§ 13b Abs. 1) – Bemessungsgrundlage",
    "47": "Leistungen von EU-Unternehmern (§ 13b Abs. 1) – Steuer",
    "84": "Andere Leistungen (§ 13b Abs. 2) – Bemessungsgrundlage",
    "85": "Andere Leistungen (§ 13b Abs. 2) – Steuer",
    "66": "Vorsteuer aus Rechnungen anderer Unternehmer",
    "61": "Vorsteuer aus innergemeinschaftlichem Erwerb",
    "62": "Entstandene Einfuhrumsatzsteuer",
    "67": "Vorsteuer aus Leistungen nach § 13b",
    "39": "Abzug der festgesetzten Sondervorauszahlung (Dauerfristverlängerung)",
    "83": "Verbleibende Umsatzsteuer-Vorauszahlung / Überschuss (−)",
}
FORM_RATES = {"81": Decimal("19"), "86": Decimal("7"), "89": Decimal("19")}


class PeriodSpecError(ValueError):
    pass


def months_for(spec: str) -> list[str]:
    m = _SPEC.fullmatch(spec)
    if not m:
        raise PeriodSpecError("Zeitraum im Format JJJJ-MM oder JJJJ-Q1 … JJJJ-Q4 angeben")
    year, part = m.group(1), m.group(2)
    if part.startswith("Q"):
        q = int(part[1])
        return [f"{year}-{mm:02d}" for mm in (3 * q - 2, 3 * q - 1, 3 * q)]
    return [f"{year}-{part}"]


def truncate_euro(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_DOWN)


@dataclass
class _Acc:
    base: Decimal = ZERO
    tax: Decimal = ZERO
    items: list[dict[str, Any]] = field(default_factory=list)


async def _issued_invoices(conn: AsyncConnection, tenant_id: UUID, months: list[str]) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        "SELECT id, number, kind, issue_date, vat_period, total_net, total_tax, total_gross, snapshot FROM invoices "
        "WHERE tenant_id = :t AND status = 'issued' AND vat_period = ANY(:m) ORDER BY number"), {"t": tenant_id, "m": months})
    return [dict(r) for r in result.mappings()]


async def _booked_receipts(conn: AsyncConnection, tenant_id: UUID, months: list[str]) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        "SELECT r.id, r.receipt_date, r.vat_period, r.description, r.tax_case, r.net_amount, r.tax_amount, r.input_tax_amount, "
        "r.rc_tax_amount, r.expense_amount, r.deductible_amount, s.name AS supplier_name FROM receipts r "
        "JOIN suppliers s ON s.id = r.supplier_id WHERE r.tenant_id = :t AND r.status = 'booked' AND r.vat_period = ANY(:m) "
        "ORDER BY r.receipt_date"), {"t": tenant_id, "m": months})
    return [dict(r) for r in result.mappings()]


async def ustva(conn: AsyncConnection, tenant_id: UUID, spec: str) -> dict[str, Any]:
    months = months_for(spec)
    acc: dict[str, _Acc] = defaultdict(_Acc)

    from app.domain.international import to_eur

    for inv in await _issued_invoices(conn, tenant_id, months):
        fx = inv["snapshot"].get("exchange_rate")
        if fx:  # Fremdwährung: Beträge zum Kurs des Rechnungsdatums in EUR (§ 16 Abs. 6 UStG)
            inv["snapshot"] = {**inv["snapshot"], "tax_breakdown": [
                {**g, "taxable_amount": str(to_eur(g["taxable_amount"], fx)), "tax_amount": str(to_eur(g["tax_amount"], fx))}
                for g in inv["snapshot"]["tax_breakdown"]]}
        for group in inv["snapshot"]["tax_breakdown"]:
            rate = Decimal(group["rate"])
            if group["category"] in ("K", "AE", "O"):
                # steuerfreie bzw. nicht steuerbare Umsätze: nur Bemessungsgrundlage (Kz 41/21/45)
                ekz = {"K": "41", "AE": "21", "O": "45"}[group["category"]]
                acc[ekz].base += Decimal(group["taxable_amount"])
                acc[ekz].items.append({"type": "invoice", "id": str(inv["id"]), "ref": inv["number"], "date": inv["issue_date"],
                                       "base": group["taxable_amount"], "tax": "0.00"})
                continue
            kz = "81" if rate == 19 else "86" if rate == 7 else None
            if kz is None or group["category"] != "S":
                continue
            a = acc[kz]
            a.base += Decimal(group["taxable_amount"])
            a.tax += Decimal(group["tax_amount"])
            a.items.append({"type": "invoice", "id": str(inv["id"]), "ref": inv["number"], "date": inv["issue_date"],
                            "base": group["taxable_amount"], "tax": group["tax_amount"]})

    for r in await _booked_receipts(conn, tenant_id, months):
        item = {"type": "receipt", "id": str(r["id"]), "ref": f"{r['supplier_name']}: {r['description']}",
                "date": r["receipt_date"], "base": str(r["net_amount"])}
        case = r["tax_case"]
        if case in ("IN_DE_19", "IN_DE_7", "IN_BEWIRTUNG"):
            acc["66"].tax += r["input_tax_amount"]
            acc["66"].items.append({**item, "tax": str(r["input_tax_amount"])})
        elif case == "IN_EINFUHR":
            acc["62"].tax += r["input_tax_amount"]
            acc["62"].items.append({**item, "tax": str(r["input_tax_amount"])})
        elif case in ("IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE"):
            base_kz, tax_kz = ("46", "47") if case == "IN_13B_EU_SERVICE" else ("84", "85")
            acc[base_kz].base += r["net_amount"]
            acc[base_kz].items.append({**item, "tax": str(r["rc_tax_amount"])})
            acc[tax_kz].tax += r["rc_tax_amount"]
            acc["67"].tax += r["input_tax_amount"]
            acc["67"].items.append({**item, "tax": str(r["input_tax_amount"])})
        elif case == "IN_IG_ERWERB":
            acc["89"].base += r["net_amount"]
            acc["89"].tax += r["rc_tax_amount"]
            acc["89"].items.append({**item, "tax": str(r["rc_tax_amount"])})
            acc["61"].tax += r["input_tax_amount"]
            acc["61"].items.append({**item, "tax": str(r["input_tax_amount"])})

    lines: list[dict[str, Any]] = []
    output_tax = ZERO
    input_tax = ZERO
    for kz in ("41", "21", "45", "81", "86", "89", "46", "47", "84", "85", "66", "61", "62", "67"):
        entry = acc.get(kz)
        if entry is None:
            continue
        line: dict[str, Any] = {"kz": kz, "label": KZ_LABELS[kz], "items": entry.items}
        if kz in FORM_RATES:
            base = truncate_euro(entry.base)
            form_tax = round_cent(base * FORM_RATES[kz] / 100)
            line.update(base=base, tax=form_tax, tax_exact=entry.tax, rounding_difference=form_tax - entry.tax)
            output_tax += form_tax
        elif kz in ("46", "84", "41", "21", "45"):
            line.update(base=truncate_euro(entry.base))
        elif kz in ("47", "85"):
            line.update(tax=entry.tax)
            output_tax += entry.tax
        else:
            line.update(tax=entry.tax)
            input_tax += entry.tax
        lines.append(line)
    payable = output_tax - input_tax
    if months[-1].endswith("-12"):
        # Dauerfristverlängerung: die gezahlte Sondervorauszahlung wird in der letzten Voranmeldung des Jahres angerechnet
        svz = await conn.scalar(text("SELECT COALESCE(sum(amount), 0) FROM tax_prepayments WHERE tenant_id = :t AND tax = 'ust_svz' "
                                     "AND year = :y AND paid_on IS NOT NULL"), {"t": tenant_id, "y": int(months[-1][:4])})
        if svz:
            lines.append({"kz": "39", "label": KZ_LABELS["39"], "tax": Decimal(svz), "items": []})
            payable -= Decimal(svz)
    lines.append({"kz": "83", "label": KZ_LABELS["83"], "tax": payable, "items": []})
    return {"period": spec, "months": months, "lines": lines, "output_tax": output_tax, "input_tax": input_tax,
            "payable": payable}


async def year_overview(conn: AsyncConnection, tenant_id: UUID, year: int, *, trade_tax_multiplier: int,
                        is_ug: bool) -> dict[str, Any]:
    months = [f"{year}-{m:02d}" for m in range(1, 13)]
    per_month: dict[str, dict[str, Decimal]] = {
        m: {"revenue": ZERO, "output_tax": ZERO, "expense": ZERO, "deductible": ZERO, "input_tax": ZERO, "rc_tax": ZERO}
        for m in months
    }
    for inv in await _issued_invoices(conn, tenant_id, months):
        row = per_month[inv["vat_period"]]
        row["revenue"] += inv["total_net"]
        row["output_tax"] += inv["total_tax"]
    for r in await _booked_receipts(conn, tenant_id, months):
        row = per_month[r["vat_period"]]
        row["expense"] += r["expense_amount"]
        row["deductible"] += r["deductible_amount"]
        row["input_tax"] += r["input_tax_amount"]
        row["rc_tax"] += r["rc_tax_amount"]
    table = []
    totals = dict.fromkeys(("revenue", "output_tax", "expense", "deductible", "input_tax", "rc_tax"), ZERO)
    for m in months:
        row = per_month[m]
        for k, v in row.items():
            totals[k] += v
        table.append({"month": m, **row, "profit": row["revenue"] - row["expense"],
                      "vat_payable": row["output_tax"] + row["rc_tax"] - row["input_tax"]})
    tax_lcf, commercial_lcf = await loss_carryforwards(conn, tenant_id, year, trade_tax_multiplier=trade_tax_multiplier, is_ug=is_ug)
    estimate = estimate_income_taxes(year=year, revenue=totals["revenue"], expense=totals["expense"],
                                     deductible=totals["deductible"], trade_tax_multiplier=Decimal(trade_tax_multiplier),
                                     is_ug=is_ug, tax_loss_carryforward=tax_lcf, commercial_loss_carryforward=commercial_lcf)
    vat_payable = totals["output_tax"] + totals["rc_tax"] - totals["input_tax"]
    return {
        "year": year,
        "months": table,
        "totals": {**totals, "profit": totals["revenue"] - totals["expense"], "vat_payable": vat_payable},
        "income_tax": {
            "profit_before_tax": estimate.profit_before_tax, "taxable_income": estimate.taxable_income,
            "kst_rate": estimate.kst_rate, "kst": estimate.kst, "solz": estimate.solz, "gewst_base": estimate.gewst_base,
            "gewst": estimate.gewst, "total": estimate.total, "profit_after_tax": estimate.profit_after_tax,
            "ug_reserve": estimate.ug_reserve, "trade_tax_multiplier": trade_tax_multiplier,
            "tax_loss_carryforward": tax_lcf, "tax_loss_used": estimate.tax_loss_used,
            "commercial_loss_carryforward": commercial_lcf, "commercial_loss_used": estimate.commercial_loss_used,
        },
        "reserve_total": estimate.total + max(vat_payable, ZERO),
    }


async def _year_base(conn: AsyncConnection, tenant_id: UUID, year: int) -> tuple[Decimal, Decimal, Decimal]:
    months = [f"{year}-{m:02d}" for m in range(1, 13)]
    revenue = sum((inv["total_net"] for inv in await _issued_invoices(conn, tenant_id, months)), ZERO)
    receipts = await _booked_receipts(conn, tenant_id, months)
    return revenue, sum((r["expense_amount"] for r in receipts), ZERO), sum((r["deductible_amount"] for r in receipts), ZERO)


async def loss_carryforwards(conn: AsyncConnection, tenant_id: UUID, year: int, *, trade_tax_multiplier: int,
                             is_ug: bool) -> tuple[Decimal, Decimal]:
    """Verlustvorträge zum Beginn von ``year`` aus allen Vorjahren seit der ersten Aktivität.

    Steuerlich (zu versteuerndes Einkommen) und handelsrechtlich (Jahresergebnis nach Steuern, für die UG-Rücklage).
    ⚠️ Näherung ohne Eröffnungsbilanz: Verluste aus der Zeit vor OpenEmployed bitte mit dem Steuerberater abgleichen.
    """
    active = await active_months(conn, tenant_id)
    if not active:
        return ZERO, ZERO
    first = int(min(active)[:4])
    tax_lcf = commercial_lcf = ZERO
    for y in range(first, year):
        revenue, expense, deductible = await _year_base(conn, tenant_id, y)
        est = estimate_income_taxes(year=y, revenue=revenue, expense=expense, deductible=deductible,
                                    trade_tax_multiplier=Decimal(trade_tax_multiplier), is_ug=is_ug,
                                    tax_loss_carryforward=tax_lcf, commercial_loss_carryforward=commercial_lcf)
        raw_taxable = revenue - deductible
        tax_lcf = tax_lcf + (-raw_taxable) if raw_taxable < 0 else tax_lcf - est.tax_loss_used
        commercial_lcf = commercial_lcf + (-est.profit_after_tax) if est.profit_after_tax < 0 \
            else commercial_lcf - est.commercial_loss_used
    return tax_lcf, commercial_lcf


async def active_months(conn: AsyncConnection, tenant_id: UUID) -> set[str]:
    """Monate mit festgeschriebenen Rechnungen oder gebuchten Belegen."""
    result = await conn.execute(text(
        "SELECT vat_period FROM invoices WHERE tenant_id = :t AND status = 'issued' "
        "UNION SELECT vat_period FROM receipts WHERE tenant_id = :t AND status = 'booked'"), {"t": tenant_id})
    return {str(r[0]) for r in result if r[0]}


def calendar(year: int, *, filing: str, extension: bool, locked: set[str], active: set[str],
             today: dt.date) -> list[dict[str, Any]]:
    first_active = min(active) if active else None
    out = []
    for d in deadlines(year, filing=filing, extension=extension):  # type: ignore[arg-type]
        done = False
        is_active = False
        due_relevant = False
        if d.kind == "ustva" and d.period:
            months = months_for(d.period)
            done = all(m in locked for m in months)
            is_active = any(m in active for m in months)
            # Pflicht zur Abgabe ab dem ersten Monat mit Umsätzen/Belegen (auch Nullmeldungen danach)
            due_relevant = first_active is not None and months[-1] >= first_active
        out.append({"due": d.due, "kind": d.kind, "label": d.label, "period": d.period, "done": done,
                    "active": is_active, "overdue": d.kind == "ustva" and due_relevant and not done and d.due < today})
    return out
