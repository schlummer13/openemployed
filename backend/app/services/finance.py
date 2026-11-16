"""Liquidität aus Sicht der Geschäftsführung: Was steht wirklich zur Verfügung?

Verfügbar = Kontostand − offene USt-Zahllast (nicht festgeschriebene Monate) − Ertragsteuer-Rücklage (KSt/Soli/GewSt
des laufenden Jahres) − offene Lieferantenrechnungen − in den nächsten 30 Tagen fällige Gehälter (AG-Kosten) und
Fixkosten. Der Kontostand stammt aus dem zuletzt erfassten Saldo (manuell, CAMT-Kontoauszug, FinTS) zuzüglich danach importierter
Umsätze; fehlt er, wird ersatzweise der Saldo des Bankkontos laut Buchhaltung verwendet (gekennzeichnet).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

D = Decimal
ZERO = D("0.00")


async def settings(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text("SELECT min_liquidity, monthly_fixed_costs, offer_followup_days, offer_auto_reminder, welcome_mail "
                                   "FROM finance_settings WHERE tenant_id = :t"), {"t": tenant_id})).mappings().one_or_none()
    return dict(row) if row else {"min_liquidity": D("5000.00"), "monthly_fixed_costs": ZERO, "offer_followup_days": 7,
                                  "offer_auto_reminder": False, "welcome_mail": True}


async def save_settings(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str) -> dict[str, Any]:
    from app.services import audit

    await conn.execute(text(
        "INSERT INTO finance_settings (tenant_id, min_liquidity, monthly_fixed_costs, offer_followup_days, offer_auto_reminder, "
        "welcome_mail, updated_by) VALUES (:t, :min_liquidity, :monthly_fixed_costs, :offer_followup_days, :offer_auto_reminder, "
        ":welcome_mail, :a) "
        "ON CONFLICT (tenant_id) DO UPDATE SET min_liquidity = EXCLUDED.min_liquidity, monthly_fixed_costs = EXCLUDED.monthly_fixed_costs, "
        "offer_followup_days = EXCLUDED.offer_followup_days, offer_auto_reminder = EXCLUDED.offer_auto_reminder, "
        "welcome_mail = EXCLUDED.welcome_mail, updated_by = EXCLUDED.updated_by, updated_at = now()"), data | {"t": tenant_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="finance_settings.saved", entity_type="finance_settings",
                       entity_id=None, after=data)
    return await settings(conn, tenant_id)


async def record_balance(conn: AsyncConnection, tenant_id: UUID, balance_on: dt.date, amount: Decimal, source: str, actor: str,
                         iban: str | None = None) -> None:
    await conn.execute(text("INSERT INTO bank_balances (tenant_id, balance_on, amount, account_iban, source, created_by) "
                            "VALUES (:t, :d, :a, :i, :s, :by)"),
                       {"t": tenant_id, "d": balance_on, "a": amount, "i": iban, "s": source, "by": actor})


async def bank_balance(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text("SELECT balance_on, amount, source FROM bank_balances WHERE tenant_id = :t "
                                   "ORDER BY balance_on DESC, created_at DESC LIMIT 1"), {"t": tenant_id})).mappings().one_or_none()
    if row is not None:
        after = D(await conn.scalar(text("SELECT COALESCE(SUM(amount), 0) FROM bank_transactions WHERE tenant_id = :t AND booked_on > :d"),
                                    {"t": tenant_id, "d": row["balance_on"]}))
        return {"amount": D(row["amount"]) + after, "as_of": row["balance_on"], "source": row["source"], "estimated": False,
                "movements_after": after}
    from app.services.accounting import acc, chart_of

    chart = await chart_of(conn, tenant_id)
    booked = D(await conn.scalar(text(
        "SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id "
        "WHERE j.tenant_id = :t AND l.account = :a"), {"t": tenant_id, "a": acc(chart, "bank")}))
    return {"amount": booked, "as_of": None, "source": "journal", "estimated": True, "movements_after": ZERO}


async def capital(conn: AsyncConnection, tenant_id: UUID, today: dt.date, ledger: Any = None) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.services import tax_report, team

    s = await settings(conn, tenant_id)
    balance = await bank_balance(conn, tenant_id)
    locked = set(await ledger.locked_periods(tenant_id=tenant_id)) if ledger is not None else set()
    active = sorted(m for m in await tax_report.active_months(conn, tenant_id) if m not in locked and m <= f"{today:%Y-%m}")
    vat_due = ZERO
    for m in active:
        vat_due += D((await tax_report.ustva(conn, tenant_id, m))["payable"])
    overview = await tax_report.year_overview(conn, tenant_id, today.year, trade_tax_multiplier=get_settings().trade_tax_multiplier,
                                              is_ug=True)
    income_tax = D(overview["income_tax"]["total"])
    payables = D(await conn.scalar(text(
        "SELECT COALESCE(SUM(CASE WHEN tax_case IN ('IN_13B_EU_SERVICE','IN_13B_DRITTLAND_SERVICE','IN_IG_ERWERB') THEN net_amount "
        "ELSE gross_amount END), 0) FROM receipts WHERE tenant_id = :t AND status = 'booked' AND kind = 'expense' AND paid_on IS NULL "
        "AND reversed_by IS NULL"), {"t": tenant_id}))
    holders = await team.list_shareholders(conn, tenant_id, today + dt.timedelta(days=30))
    salaries = sum((D(h["salary"]["employer_cost_month"]) for h in holders if h.get("salary") and h["is_managing_director"]), ZERO)
    fixed = D(s["monthly_fixed_costs"])
    reserved = max(vat_due, ZERO) + income_tax + payables + salaries + fixed
    available = balance["amount"] - reserved
    return {
        "balance": balance, "vat_due": vat_due, "vat_months": active, "income_tax_reserve": income_tax, "payables": payables,
        "salaries_30d": salaries, "fixed_costs_30d": fixed, "reserved": reserved, "available": available,
        "min_liquidity": D(s["min_liquidity"]), "below_minimum": available < D(s["min_liquidity"]),
        "ug_reserve_note": D(overview["income_tax"]["ug_reserve"]),
    }


async def monthly(conn: AsyncConnection, tenant_id: UUID, today: dt.date, months: int = 12) -> list[dict[str, Any]]:
    """Einnahmen (Netto-Umsatz) und Ausgaben (Aufwand laut Belegen + geplante Gehälter) je Monat, plus Zahlungseingänge."""
    from app.services import team

    idx = today.year * 12 + today.month - 1 - (months - 1)
    start = dt.date(idx // 12, idx % 12 + 1, 1)
    rev = {r[0]: D(r[1]) for r in await conn.execute(text(
        "SELECT to_char(issue_date, 'YYYY-MM'), SUM(total_net) FROM invoices WHERE tenant_id = :t AND status = 'issued' "
        "AND issue_date >= :s GROUP BY 1"), {"t": tenant_id, "s": start})}
    exp = {r[0]: D(r[1]) for r in await conn.execute(text(
        "SELECT to_char(receipt_date, 'YYYY-MM'), SUM(expense_amount) FROM receipts WHERE tenant_id = :t AND status = 'booked' "
        "AND receipt_date >= :s GROUP BY 1"), {"t": tenant_id, "s": start})}
    cash_in = {r[0]: D(r[1]) for r in await conn.execute(text(
        "SELECT to_char(paid_on, 'YYYY-MM'), SUM(amount) FROM payments WHERE tenant_id = :t AND reversed_at IS NULL "
        "AND paid_on >= :s GROUP BY 1"), {"t": tenant_id, "s": start})}
    out = []
    cursor = start
    # Gehälter ändern sich nur zu Beginn einer neuen Vergütung → nur dann neu berechnen (statt 12× je Monat)
    changes = sorted({r[0] for r in await conn.execute(text(
        "SELECT valid_from FROM md_compensations c JOIN shareholders s ON s.id = c.shareholder_id WHERE s.tenant_id = :t"),
        {"t": tenant_id})})
    salaries, computed_for = ZERO, None
    while cursor <= today:
        key = f"{cursor:%Y-%m}"
        month_end = (cursor + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        signature = tuple(c for c in changes if c <= month_end)
        if signature != computed_for:
            holders = await team.list_shareholders(conn, tenant_id, cursor)
            salaries = sum((D(h["salary"]["employer_cost_month"]) for h in holders
                            if h.get("salary") and not h.get("planned") and h["is_managing_director"]), ZERO)
            computed_for = signature
        income, expenses = rev.get(key, ZERO), exp.get(key, ZERO) + salaries
        out.append({"month": key, "income": income, "expenses": expenses, "salaries": salaries, "cash_in": cash_in.get(key, ZERO),
                    "result": income - expenses})
        cursor = (cursor + dt.timedelta(days=32)).replace(day=1)
    return out


def _month_end(d: dt.date) -> dt.date:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


async def forecast(conn: AsyncConnection, tenant_id: UUID, today: dt.date, months: int = 3, ledger: Any = None,
                   payment_days: int = 14) -> dict[str, Any]:
    """Liquiditätsvorschau (laufender + folgende Monate) aus bekannten Zahlungsströmen – eine Planung, keine Buchhaltung.

    Einzahlungen: offene Rechnungen nach Fälligkeit (überfällige im laufenden Monat) und künftige Vertragsabrechnungen
    (brutto, Zahlungseingang ``payment_days`` nach Abrechnung). Auszahlungen: Gehälter inkl. Arbeitgeberanteil, Fixkosten,
    im laufenden Monat zusätzlich offene Lieferantenrechnungen und die noch nicht abgeführte Umsatzsteuer.
    Steuervorauszahlungen (KSt, GewSt, USt-Sondervorauszahlung) aus den erfassten Bescheiden nach Fälligkeit.
    """
    from app.domain.invoice_calc import LineInput, calculate_invoice
    from app.services import contracts as ct
    from app.services import receivables as rv

    cap = await capital(conn, tenant_id, today, ledger)
    start = cap["balance"]["amount"]
    first = today.replace(day=1)
    keys: list[str] = []
    d = first
    for _ in range(months):
        keys.append(f"{d:%Y-%m}")
        d = _month_end(d) + dt.timedelta(days=1)
    horizon = _month_end(dt.date.fromisoformat(f"{keys[-1]}-01"))
    rows = {k: {"month": k, "receivables": ZERO, "contracts": ZERO, "salaries": cap["salaries_30d"], "fixed_costs": cap["fixed_costs_30d"],
                "payables": ZERO, "vat": ZERO, "tax_prepayments": ZERO} for k in keys}
    rows[keys[0]]["payables"] = cap["payables"]
    rows[keys[0]]["vat"] = max(cap["vat_due"], ZERO)

    from app.domain.international import to_eur

    fx = {r[0]: r[1] for r in await conn.execute(text(
        "SELECT id, exchange_rate FROM invoices WHERE tenant_id = :t AND exchange_rate IS NOT NULL"), {"t": tenant_id})}
    for item in (await rv.open_items(conn, tenant_id, today))["items"]:
        key = max(f"{item['due_date']:%Y-%m}", keys[0])
        if key in rows:
            rows[key]["receivables"] += to_eur(item["principal_open"], fx.get(item["invoice_id"]))

    for c in await ct.list_contracts(conn, tenant_id):
        if c["status"] != "active" or not c["lines"]:
            continue
        gross = calculate_invoice([LineInput(x["description"], x["quantity"], x["unit_code"], x["unit_price"], x["tax_case"])
                                   for x in c["lines"]], ct._price_mode(c["customer_type"])).total_gross
        for p in ct.schedule(c, horizon):
            # Nur künftige Abrechnungen (ab laufendem Monat): Alt-Zeiträume ohne Rechnung wären eine zu optimistische Annahme
            if p["billed"] or p["bill_on"] < first:
                continue
            key = f"{max(p['bill_on'], today) + dt.timedelta(days=payment_days):%Y-%m}"
            if key in rows:
                rows[key]["contracts"] += (gross * D(p["factor"])).quantize(D("0.01"))

    from app.services.reports import open_prepayments

    for pp in await open_prepayments(conn, tenant_id, horizon):
        key = max(f"{pp['due_date']:%Y-%m}", keys[0])
        if key in rows:
            rows[key]["tax_prepayments"] += D(pp["total"])

    out, balance = [], start
    for k in keys:
        r = rows[k]
        inflow = r["receivables"] + r["contracts"]
        outflow = r["salaries"] + r["fixed_costs"] + r["payables"] + r["vat"] + r["tax_prepayments"]
        balance = balance + inflow - outflow
        out.append(r | {"inflow": inflow, "outflow": outflow, "net": inflow - outflow, "closing": balance})
    return {"start": start, "estimated": cap["balance"]["estimated"], "months": out, "min_liquidity": cap["min_liquidity"],
            "lowest": min((m["closing"] for m in out), default=start)}
