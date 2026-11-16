"""Auswertungen und Meldungen: BWA mit Vorjahresvergleich, Zusammenfassende Meldung, Steuervorauszahlungen, Monatsabschluss."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import ROUND_DOWN, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
ZERO = D("0.00")
TAX_NS = uuid.UUID("7f3e1c5a-2b9d-4e60-8a14-6c0d9b2e5f37")

# ───────────────────────── BWA (Kurzfristige Erfolgsrechnung, Aufbau wie DATEV-BWA Form 01) ─────────────────────────

BWA_ROWS: list[tuple[str, str]] = [
    ("revenue", "Umsatzerlöse"), ("other_income", "Sonstige betriebliche Erlöse"), ("total_output", "Gesamtleistung"),
    ("material", "Material-/Wareneinkauf, Fremdleistungen"), ("gross_profit", "Rohertrag"),
    ("personnel", "Personalkosten"), ("rooms", "Raumkosten"), ("operating_taxes", "Betriebliche Steuern"),
    ("insurance", "Versicherungen/Beiträge"), ("vehicles", "Kfz-Kosten (ohne Steuer)"), ("marketing", "Werbe-/Reisekosten"),
    ("delivery", "Kosten Warenabgabe"), ("depreciation", "Abschreibungen"), ("repairs", "Reparatur/Instandhaltung"),
    ("other_costs", "Sonstige Kosten"), ("total_costs", "Gesamtkosten"), ("operating_result", "Betriebsergebnis"),
    ("interest", "Zinsaufwand"), ("neutral", "Sonstiger neutraler Aufwand/Ertrag"), ("result_before_tax", "Ergebnis vor Steuern"),
    ("income_taxes", "Steuern vom Einkommen und Ertrag"), ("result", "Vorläufiges Ergebnis"),
]
SUMS = {"total_output", "gross_profit", "total_costs", "operating_result", "result_before_tax", "result"}


def bwa_group(chart: str, account: str, acc_type: str | None) -> str | None:
    """Kontenzuordnung nach Kontenbereichen (SKR04 / SKR03)."""
    n = int(account)
    if chart == "SKR04":
        if 4000 <= n <= 4799:
            return "revenue"
        if 4800 <= n <= 4999:
            return "other_income"
        if 5000 <= n <= 5999:
            return "material"
        if 6000 <= n <= 6199:
            return "personnel"
        if 6200 <= n <= 6299:
            return "depreciation"
        if 6300 <= n <= 6399:
            return "rooms"
        if 6400 <= n <= 6449:
            return "insurance"
        if 6450 <= n <= 6499:
            return "repairs"
        if 6500 <= n <= 6599:
            return "vehicles"
        if 6600 <= n <= 6699:
            return "marketing"
        if 6700 <= n <= 6799:
            return "delivery"
        if 6800 <= n <= 6879:
            return "other_costs"
        if 6880 <= n <= 6999 or 7000 <= n <= 7299 or 7400 <= n <= 7599:
            return "neutral"
        if 7300 <= n <= 7399:
            return "interest"
        if 7600 <= n <= 7649:
            return "income_taxes"
        if 7650 <= n <= 7699:
            return "operating_taxes"
        return None
    if 8000 <= n <= 8799:
        return "revenue"
    if 8800 <= n <= 8999 or 2700 <= n <= 2749:
        return "other_income"
    if 3000 <= n <= 3999:
        return "material"
    if 4100 <= n <= 4199:
        return "personnel"
    if 4200 <= n <= 4299:
        return "rooms"
    if n in (4320,) or 2200 <= n <= 2289:
        return "income_taxes"
    if 4300 <= n <= 4359:
        return "operating_taxes"
    if 4360 <= n <= 4399:
        return "insurance"
    if 4500 <= n <= 4599:
        return "vehicles"
    if 4600 <= n <= 4699:
        return "marketing"
    if 4700 <= n <= 4799:
        return "delivery"
    if 4800 <= n <= 4819:
        return "repairs"
    if 4820 <= n <= 4899:
        return "depreciation"
    if 4900 <= n <= 4999:
        return "other_costs"
    if 2100 <= n <= 2149:
        return "interest"
    if 2000 <= n <= 2999:
        return "neutral"
    return None


async def _group_sums(conn: AsyncConnection, tenant_id: UUID, chart: str, start: dt.date, end: dt.date) -> dict[str, D]:
    rows = await conn.execute(text(
        "SELECT l.account, a.type, SUM(l.credit) - SUM(l.debit) AS amount FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id "
        "LEFT JOIN accounts a ON a.chart = j.chart AND a.number = l.account WHERE j.tenant_id = :t AND j.entry_date BETWEEN :s AND :e "
        "AND j.closing = false AND COALESCE(a.type, '') IN ('revenue', 'expense') GROUP BY l.account, a.type"),
        {"t": tenant_id, "s": start, "e": end})
    out: dict[str, D] = {k: ZERO for k, _ in BWA_ROWS}
    for r in rows:
        g = bwa_group(chart, r.account, r.type)
        if g is None:
            g = "other_income" if r.type == "revenue" else "other_costs"
        out[g] += D(r.amount)  # Erträge positiv, Aufwand negativ
    return out


def _derive(v: dict[str, D]) -> dict[str, D]:
    """Zeilenwerte für die Anzeige: Kosten positiv, Summen berechnet."""
    cost_keys = ("personnel", "rooms", "operating_taxes", "insurance", "vehicles", "marketing", "delivery", "depreciation", "repairs",
                 "other_costs")
    r = {k: v.get(k, ZERO) for k, _ in BWA_ROWS}
    out = dict(r)
    for k in ("material", *cost_keys, "interest", "income_taxes"):
        out[k] = -r[k]
    out["total_output"] = r["revenue"] + r["other_income"]
    out["gross_profit"] = out["total_output"] - out["material"]
    out["total_costs"] = sum((out[k] for k in cost_keys), ZERO)
    out["operating_result"] = out["gross_profit"] - out["total_costs"]
    out["result_before_tax"] = out["operating_result"] - out["interest"] + r["neutral"]
    out["result"] = out["result_before_tax"] - out["income_taxes"]
    return out


def _month_end(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


async def bwa(conn: AsyncConnection, tenant_id: UUID, period: str) -> dict[str, Any]:
    from app.services.accounting import chart_of

    y, m = (int(x) for x in period.split("-"))
    chart = await chart_of(conn, tenant_id)
    first, last = dt.date(y, m, 1), _month_end(dt.date(y, m, 1))
    py_first, py_last = dt.date(y - 1, m, 1), _month_end(dt.date(y - 1, m, 1))
    cols = {
        "month": _derive(await _group_sums(conn, tenant_id, chart, first, last)),
        "ytd": _derive(await _group_sums(conn, tenant_id, chart, dt.date(y, 1, 1), last)),
        "prev_month": _derive(await _group_sums(conn, tenant_id, chart, py_first, py_last)),
        "prev_ytd": _derive(await _group_sums(conn, tenant_id, chart, dt.date(y - 1, 1, 1), py_last)),
    }
    rows = []
    for key, label in BWA_ROWS:
        row: dict[str, Any] = {"key": key, "label": label, "sum": key in SUMS}
        for c, vals in cols.items():
            row[c] = vals[key]
            base = vals["total_output"]
            row[f"{c}_pct"] = (vals[key] / base * 100).quantize(D("0.1")) if base else None
        row["delta_month"] = row["month"] - row["prev_month"]
        row["delta_ytd"] = row["ytd"] - row["prev_ytd"]
        rows.append(row)
    # Monatsverlauf (12 Monate) für das Diagramm
    trend = []
    for i in range(11, -1, -1):
        mm = (m - i - 1) % 12 + 1
        yy = y + (m - i - 1) // 12
        s = dt.date(yy, mm, 1)
        v = _derive(await _group_sums(conn, tenant_id, chart, s, _month_end(s)))
        trend.append({"month": f"{yy}-{mm:02d}", "output": v["total_output"], "costs": v["total_costs"] + v["material"],
                      "result": v["result"]})
    return {"period": period, "chart": chart, "rows": rows, "trend": trend,
            "note": "Vorläufig aus dem Journal (ohne Abschlussbuchungen) – Abgrenzungen und Abschreibungen fallen meist erst "
                    "zum Jahresende an."}


# ───────────────────────── Zusammenfassende Meldung (§ 18a UStG) ─────────────────────────

def zm_periods(year: int) -> list[str]:
    return [f"{year}-Q{q}" for q in range(1, 5)] + [f"{year}-{m:02d}" for m in range(1, 13)]


async def zm(conn: AsyncConnection, tenant_id: UUID, spec: str) -> dict[str, Any]:
    """Innergemeinschaftliche Lieferungen (L) und sonstige Leistungen (S) je USt-IdNr. des Abnehmers."""
    from app.services.tax_report import months_for

    months = months_for(spec)
    rows = await conn.execute(text(
        "SELECT id, number, kind, snapshot FROM invoices WHERE tenant_id = :t AND status = 'issued' AND vat_period = ANY(:m)"),
        {"t": tenant_id, "m": months})
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    missing: list[str] = []
    for r in rows.mappings():
        snap = r["snapshot"]
        buyer = snap.get("buyer") or {}
        vat = (buyer.get("vat_id") or "").replace(" ", "").upper()
        for g in snap.get("tax_breakdown", []):
            kind = {"K": "L", "AE": "S"}.get(g["category"])
            if kind is None:
                continue
            if g["category"] == "AE" and vat.startswith("DE"):
                continue  # § 13b im Inland gehört nicht in die ZM
            if not vat or len(vat) < 4:
                missing.append(r["number"])
                continue
            key = (vat, kind)
            item = acc.setdefault(key, {"country": vat[:2], "vat_id": vat[2:], "kind": kind, "amount": ZERO, "invoices": [],
                                        "name": buyer.get("name")})
            from app.domain.international import to_eur

            item["amount"] += to_eur(g["taxable_amount"], snap.get("exchange_rate"))  # Storno negativ; Fremdwährung → EUR
            item["invoices"].append(r["number"])
    lines = sorted(acc.values(), key=lambda x: (x["country"], x["vat_id"], x["kind"]))
    for ln in lines:
        ln["amount_euro"] = ln["amount"].to_integral_value(rounding=ROUND_DOWN)
    return {"period": spec, "months": months, "lines": lines, "total": sum((x["amount"] for x in lines), ZERO),
            "missing_vat_id": sorted(set(missing))}


def zm_csv(data: dict[str, Any]) -> str:
    """CSV für den Upload im BZStOnline-Portal (Länderkennzeichen, USt-IdNr., Betrag in vollen Euro, Art der Leistung)."""
    out = ["Laenderkennzeichen,USt-IdNr.,Betrag(EUR),Art der Leistung"]
    for ln in data["lines"]:
        out.append(f"{ln['country']},{ln['vat_id']},{ln['amount_euro']},{ln['kind']}")
    return "\r\n".join(out) + "\r\n"


# ───────────────────────── Steuervorauszahlungen ─────────────────────────

KST_DUE = ((3, 10), (6, 10), (9, 10), (12, 10))
GEWST_DUE = ((2, 15), (5, 15), (8, 15), (11, 15))


async def vat_extension(conn: AsyncConnection, tenant_id: UUID) -> bool:
    from app.core.config import get_settings

    v = await conn.scalar(text("SELECT vat_extension FROM tax_settings WHERE tenant_id = :t"), {"t": tenant_id})
    return bool(get_settings().vat_extension if v is None else v)


async def set_vat_extension(conn: AsyncConnection, tenant_id: UUID, enabled: bool, actor: str) -> None:
    await conn.execute(text("INSERT INTO tax_settings (tenant_id, vat_extension, updated_by) VALUES (:t, :v, :a) "
                            "ON CONFLICT (tenant_id) DO UPDATE SET vat_extension = :v, updated_by = :a, updated_at = now()"),
                       {"t": tenant_id, "v": enabled, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="tax.vat_extension", entity_type="tenant", entity_id=tenant_id,
                       after={"enabled": enabled})


async def prepayments(conn: AsyncConnection, tenant_id: UUID, year: int) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, tax, year, due_date, amount, soli, note, paid_on, journal_entry_id FROM tax_prepayments WHERE tenant_id = :t AND year "
        "= :y "
        "ORDER BY due_date, tax"), {"t": tenant_id, "y": year})
    return [dict(r) for r in rows.mappings()]


async def open_prepayments(conn: AsyncConnection, tenant_id: UUID, until: dt.date) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, tax, due_date, amount + soli AS total FROM tax_prepayments WHERE tenant_id = :t AND paid_on IS NULL AND due_date <= :u "
        "ORDER BY due_date"), {"t": tenant_id, "u": until})
    return [dict(r) for r in rows.mappings()]


async def set_schedule(conn: AsyncConnection, tenant_id: UUID, *, tax: str, year: int, quarterly: D, soli: D, actor: str) -> None:
    """Vorauszahlungen laut Bescheid (vierteljährlich) – ersetzt noch nicht bezahlte Termine des Jahres."""
    from app.domain.tax_calendar import next_business_day

    if tax not in ("kst", "gewst"):
        raise ConflictError("Nur Körperschaft- und Gewerbesteuer haben Quartalstermine")
    await conn.execute(text("DELETE FROM tax_prepayments WHERE tenant_id = :t AND tax = :x AND year = :y AND paid_on IS NULL"),
                       {"t": tenant_id, "x": tax, "y": year})
    if quarterly > 0:
        for month, day in (KST_DUE if tax == "kst" else GEWST_DUE):
            due = next_business_day(dt.date(year, month, day))
            await conn.execute(text(
                "INSERT INTO tax_prepayments (tenant_id, tax, year, due_date, amount, soli, note, created_by) VALUES (:t, :x, :y, :d, :a, "
                ":s, "
                "'laut Vorauszahlungsbescheid', :by) ON CONFLICT (tenant_id, tax, due_date) DO NOTHING"),
                {"t": tenant_id, "x": tax, "y": year, "d": due, "a": quarterly, "s": soli if tax == "kst" else ZERO, "by": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="tax.prepayments_set", entity_type="tax", entity_id=f"{tax}-{year}",
                       after={"quarterly": str(quarterly), "soli": str(soli)})


async def svz_proposal(conn: AsyncConnection, tenant_id: UUID, year: int) -> dict[str, Any]:
    """Sondervorauszahlung = 1/11 der Summe der Vorauszahlungen des Vorjahres (nur Monatszahler mit Dauerfristverlängerung)."""
    from app.services.tax_report import ustva

    total = ZERO
    for m in range(1, 13):
        r = await ustva(conn, tenant_id, f"{year - 1}-{m:02d}")
        total += max(D(r["payable"]), ZERO) if m < 12 else D(r["payable"]) + sum(
            (D(ln["tax"]) for ln in r["lines"] if ln["kz"] == "39"), ZERO)
    amount = (max(total, ZERO) / 11).quantize(D("1"), rounding=ROUND_DOWN)
    return {"year": year, "basis": total, "amount": amount}


async def set_svz(conn: AsyncConnection, tenant_id: UUID, year: int, amount: D, actor: str) -> None:
    from app.domain.tax_calendar import next_business_day

    due = next_business_day(dt.date(year, 2, 10))
    existing = (await conn.execute(text("SELECT id, paid_on FROM tax_prepayments WHERE tenant_id = :t AND tax = 'ust_svz' AND year = :y"),
                                   {"t": tenant_id, "y": year})).one_or_none()
    if existing and existing[1]:
        raise ConflictError("Sondervorauszahlung ist bereits bezahlt gebucht")
    if existing:
        await conn.execute(text("UPDATE tax_prepayments SET amount = :a WHERE id = :id"), {"a": amount, "id": existing[0]})
    else:
        await conn.execute(text("INSERT INTO tax_prepayments (tenant_id, tax, year, due_date, amount, note, created_by) VALUES "
                                "(:t, 'ust_svz', :y, :d, :a, 'Dauerfristverlängerung', :by)"),
                           {"t": tenant_id, "y": year, "d": due, "a": amount, "by": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="tax.svz_set", entity_type="tax", entity_id=f"svz-{year}",
                       after={"amount": str(amount)})


async def pay_prepayment(conn: AsyncConnection, ledger: Any, tenant_id: UUID, prepayment_id: UUID, paid_on: dt.date, actor: str) -> None:
    from app.services.accounting import Line, acc, chart_of, post

    p = (await conn.execute(text("SELECT * FROM tax_prepayments WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                            {"t": tenant_id, "id": prepayment_id})).mappings().one_or_none()
    if p is None:
        raise NotFoundError("Vorauszahlung nicht gefunden")
    if p["paid_on"]:
        raise ConflictError("Bereits bezahlt gebucht")
    chart = await chart_of(conn, tenant_id)
    amount, soli = D(p["amount"]), D(p["soli"])
    labels = {"kst": "Körperschaftsteuer-Vorauszahlung", "gewst": "Gewerbesteuer-Vorauszahlung", "ust_svz": "USt-Sondervorauszahlung"}
    if p["tax"] == "kst":
        lines = [Line(acc(chart, "corp_tax"), debit=amount), Line(acc(chart, "soli"), debit=soli)]
    elif p["tax"] == "gewst":
        lines = [Line(acc(chart, "trade_tax"), debit=amount)]
    else:
        lines = [Line("1781" if chart == "SKR03" else "3830", debit=amount)]
    lines.append(Line(acc(chart, "bank"), credit=amount + soli))
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=paid_on, description=f"{labels[p['tax']]} {p['due_date']:%m/%Y}",
                       document_ref=f"VZ-{p['tax'].upper()}-{p['due_date']:%Y%m}", source_type="tax_payment",
                       source_id=uuid.uuid5(TAX_NS, str(prepayment_id)), lines=lines, method="rule", actor=actor)
    await conn.execute(text("UPDATE tax_prepayments SET paid_on = :d, journal_entry_id = :e WHERE id = :id"),
                       {"d": paid_on, "e": entry, "id": prepayment_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="tax.prepayment_paid", entity_type="tax_prepayment",
                       entity_id=prepayment_id, after={"paid_on": str(paid_on), "amount": str(amount + soli)})


# ───────────────────────── Monatsabschluss ─────────────────────────

async def month_checklist(conn: AsyncConnection, tenant_id: UUID, ledger: Any, period: str, today: dt.date) -> dict[str, Any]:
    from app.services import closing
    from app.services.tax_report import months_for

    start, end = closing.month_bounds(period)
    p = {"t": tenant_id, "s": start, "e": end}
    open_items = {i["kind"]: i for i in await closing.open_items(conn, tenant_id, [period])}
    last_tx = await conn.scalar(text("SELECT max(booked_on) FROM bank_transactions WHERE tenant_id = :t"), p)
    last_bal = await conn.scalar(text("SELECT max(balance_on) FROM bank_balances WHERE tenant_id = :t"), p)
    covered = max([d for d in (last_tx, last_bal) if d], default=None)
    has_payroll = bool(await conn.scalar(text(
        "SELECT 1 FROM employees WHERE tenant_id = :t AND archived_at IS NULL AND entry_date <= :e AND (exit_date IS NULL OR exit_date >= "
        ":s) "
        "UNION SELECT 1 FROM md_compensations m JOIN shareholders s ON s.id = m.shareholder_id WHERE s.tenant_id = :t AND m.valid_from <= "
        ":e "
        "AND m.gross_month > 0 LIMIT 1"), p))
    run = (await conn.execute(text("SELECT status FROM payroll_runs WHERE tenant_id = :t AND period = :p"),
                              {"t": tenant_id, "p": period})).scalar_one_or_none()
    overdue_prepay = await conn.scalar(text("SELECT count(*) FROM tax_prepayments WHERE tenant_id = :t AND paid_on IS NULL AND due_date "
                                            "<= :e"), p)
    locked = set(await ledger.locked_periods(tenant_id=tenant_id))
    from app.core.config import get_settings

    filing = get_settings().vat_filing
    vat_spec = period if filing == "monthly" else (f"{period[:4]}-Q{(int(period[5:]) - 1) // 3 + 1}" if int(period[5:]) % 3 == 0 else None)
    items = [
        {"key": "bank", "label": "Kontoauszüge bis Monatsende importiert", "ok": bool(covered and covered >= end), "required": True,
         "hint": f"letzter Umsatz/Saldo: {covered:%d.%m.%Y}" if covered else "noch kein Kontoauszug", "href": "/buchhaltung?tab=bank"},
        {"key": "bank_open", "label": "Bankumsätze zugeordnet", "ok": "bank_open" not in open_items, "required": True,
         "hint": open_items.get("bank_open", {}).get("text", "alle zugeordnet"), "href": "/buchhaltung?tab=bank"},
        {"key": "receipts", "label": "Belege des Monats gebucht",
         "ok": "receipt_drafts" not in open_items and "files_unassigned" not in open_items,
         "required": True, "hint": " · ".join(i["text"] for k, i in open_items.items() if k in ("receipt_drafts", "files_unassigned"))
         or "alles gebucht", "href": "/belege"},
        {"key": "invoices", "label": "Leistungen des Monats abgerechnet", "ok": "invoice_drafts" not in open_items, "required": True,
         "hint": open_items.get("invoice_drafts", {}).get("text", "keine offenen Entwürfe"), "href": "/rechnungen"},
        {"key": "payroll", "label": "Lohnlauf freigegeben", "ok": (run == "approved") or not has_payroll, "required": has_payroll,
         "hint": "keine Gehälter" if not has_payroll else ("freigegeben" if run == "approved" else "noch nicht freigegeben"),
         "href": f"/mitarbeiter?tab=lohn&period={period}"},
        {"key": "prepayments", "label": "Fällige Steuervorauszahlungen bezahlt", "ok": not overdue_prepay, "required": False,
         "hint": f"{overdue_prepay} offen" if overdue_prepay else "keine offen", "href": "/steuern?tab=vorauszahlungen"},
    ]
    if vat_spec:
        vat_locked = all(m in locked for m in months_for(vat_spec))
        items.append({"key": "vat", "label": f"Umsatzsteuer-Voranmeldung {vat_spec} festgeschrieben", "ok": vat_locked, "required": False,
                      "hint": "festgeschrieben" if vat_locked else "nach dem Monatsabschluss unter Steuern übermitteln",
                      "href": "/steuern"})
    closed = (await conn.execute(text("SELECT closed_by, closed_at, confirmed FROM month_closings WHERE tenant_id = :t AND period = :p"),
                                 {"t": tenant_id, "p": period})).mappings().one_or_none()
    return {"period": period, "items": items, "locked": period in locked, "closable": end < today and period not in locked,
            "closed": dict(closed) if closed else None, "done": sum(1 for i in items if i["ok"]), "total": len(items)}


async def close_month(conn: AsyncConnection, ledger: Any, tenant_id: UUID, period: str, actor: str, today: dt.date,
                      confirm_open: bool) -> dict[str, Any]:
    from app.services.accounting import ensure_postings

    await ensure_postings(conn, ledger, tenant_id, f"user:{actor}")
    check = await month_checklist(conn, tenant_id, ledger, period, today)
    if check["locked"]:
        raise ConflictError("Der Monat ist bereits festgeschrieben")
    if not check["closable"]:
        raise ConflictError("Der Monat ist noch nicht vorbei")
    missing = [i for i in check["items"] if i["required"] and not i["ok"]]
    if missing and not confirm_open:
        raise ConflictError("Offene Punkte: " + "; ".join(f"{i['label']} ({i['hint']})" for i in missing))
    confirmed = [f"{i['label']}: {i['hint']}" for i in missing]
    sealed = await ledger.seal(tenant_id=tenant_id, record_type="month.closed",
                               payload={"period": period, "open_items_confirmed": confirmed},
                               sealed_by=f"user:{actor}")
    await ledger.lock_period(tenant_id=tenant_id, period=period, sealed_by=f"user:{actor}")
    import json

    await conn.execute(text("INSERT INTO month_closings (tenant_id, period, confirmed, ledger_hash, closed_by) "
                            "VALUES (:t, :p, CAST(:c AS jsonb), :h, :a)"),
                       {"t": tenant_id, "p": period, "c": json.dumps(confirmed), "h": sealed.hash, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="month.closed", entity_type="period", entity_id=period,
                       after={"confirmed": confirmed})
    return await month_checklist(conn, tenant_id, ledger, period, today)
