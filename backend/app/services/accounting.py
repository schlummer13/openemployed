"""Doppelte Buchführung nach SKR03/SKR04.

- Ausgangsrechnungen, Stornos und Zahlungen werden nach festen Regeln gebucht (keine Ermessensfrage).
- Eingangsbelege werden auf Klick von der KI (lokal oder Claude) kontiert: Sie wählt Aufwands-/Anlagekonten (ggf. aufgeteilt) und
  begründet die Wahl; Vorsteuer, Reverse Charge, Bewirtung 70/30 und Gegenbuchung ergänzt das System nach festen
  Regeln. Vor dem Schreiben wird geprüft: Konten existieren im Kontenrahmen, Beträge stimmen exakt, Soll = Haben.
- Buchungssätze sind unveränderlich und werden im Ledger festgeschrieben.
"""

from __future__ import annotations

import copy
import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.expenses import BEWIRTUNG_DEDUCTIBLE, CATEGORIES
from app.services import audit

log = logging.getLogger("openemployed.accounting")
D = Decimal
CENT = D("0.01")
ZERO = D("0")


class AccountingError(ValueError):
    pass


# Rollen → (SKR03, SKR04)
ROLES: dict[str, tuple[str, str]] = {
    "bank": ("1200", "1800"),
    "receivables": ("1400", "1200"),
    "payables": ("1600", "3300"),
    "revenue_19": ("8400", "4400"),
    "revenue_7": ("8300", "4300"),
    "output_vat_19": ("1776", "3806"),
    "output_vat_7": ("1771", "3801"),
    "input_vat_19": ("1576", "1406"),
    "input_vat_7": ("1571", "1401"),
    "rc_input_vat_19": ("1577", "1407"),
    "rc_output_vat_19": ("1787", "3837"),
    "hospitality": ("4650", "6640"),
    "hospitality_nd": ("4654", "6644"),
    # Anlagen und Abschluss (mit dem Steuerberater abstimmen)
    "depreciation": ("4830", "6220"),
    "depreciation_intangible": ("4822", "6200"),
    "depreciation_pool": ("4862", "6264"),
    "depreciation_gwg": ("4855", "6260"),
    "asset_disposal": ("2310", "6895"),
    "trade_tax": ("4320", "7610"),
    "corp_tax": ("2200", "7600"),
    "soli": ("2208", "7608"),
    "provision_trade_tax": ("0956", "3035"),
    "provision_corp_tax": ("0955", "3040"),
    "provision_closing": ("0977", "3095"),
    "provision_other": ("0970", "3070"),
    "closing_costs": ("4957", "6827"),
    "prepaid_expenses": ("0980", "1900"),
    "deferred_income": ("0990", "3900"),
    "other_liabilities": ("1700", "3500"),
    # Reisekosten der (Gesellschafter-)Geschäftsführer als Arbeitnehmer
    "travel_other": ("4660", "6650"),
    "revenue_ig_goods": ("8125", "4125"),
    "revenue_eu_service": ("8338", "4336"),
    "revenue_third_country": ("8339", "4338"),
    "fx_gain": ("2668", "4840"),
    "fx_loss": ("2150", "6880"),
    # Lohnbuchhaltung
    "salaries": ("4120", "6020"),
    "salaries_minijob": ("4190", "6035"),
    "social_security": ("4130", "6110"),
    "flat_wage_tax": ("4199", "6040"),
    "wages_payable": ("1740", "3720"),
    "wage_tax_payable": ("1741", "3730"),
    "social_security_payable": ("1742", "3740"),
    "tax_free_benefits": ("4140", "6130"),
    "employee_receivables": ("1340", "1340"),
    "benefits_offset": ("8614", "4947"),
    "travel_fares": ("4663", "6663"),
    "travel_meals": ("4664", "6664"),
    "travel_km": ("4668", "6668"),
}
CATEGORY_ACCOUNTS: dict[str, tuple[str, str]] = {
    "software": ("4964", "6837"),
    "hosting": ("4806", "6495"),
    "telecom": ("4920", "6805"),
    "advertising": ("4600", "6600"),
    "office": ("4930", "6815"),
    "hardware_gwg": ("4855", "6260"),
    "fixed_asset": ("0490", "0690"),
    "rent": ("4210", "6310"),
    "training": ("4945", "6821"),
    "literature": ("4940", "6820"),
    "consulting": ("4950", "6825"),
    "bookkeeping": ("4955", "6830"),
    "insurance": ("4360", "6400"),
    "fees": ("4380", "6420"),
    "bank_fees": ("4970", "6855"),
    "travel": ("4670", "6670"),
    "hospitality": ("4650", "6640"),
    "gifts": ("4630", "6610"),
    "freelancer": ("3100", "5900"),
    "other": ("4900", "6300"),
}


def acc(chart: str, role: str) -> str:
    return ROLES[role][0 if chart == "SKR03" else 1]


def category_account(chart: str, category: str) -> str:
    return CATEGORY_ACCOUNTS.get(category, CATEGORY_ACCOUNTS["other"])[0 if chart == "SKR03" else 1]


async def chart_of(conn: AsyncConnection, tenant_id: UUID) -> str:
    chart = await conn.scalar(text("SELECT chart FROM datev_settings WHERE tenant_id = :t"), {"t": tenant_id})
    return str(chart or "SKR04")


async def accounts(conn: AsyncConnection, chart: str) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT number, name, type FROM accounts WHERE chart = :c ORDER BY number"), {"c": chart})
    return [dict(r) for r in rows.mappings()]


@dataclass
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    note: str | None = None


def _normalize(lines: list[Line]) -> list[Line]:
    """Negative Beträge auf die Gegenseite drehen, Nullzeilen entfernen."""
    out = []
    for ln in lines:
        d, c = ln.debit.quantize(CENT), ln.credit.quantize(CENT)
        if d < 0:
            d, c = D(0), c - d
        if c < 0:
            c, d = D(0), d - c
        net = d - c
        if net > 0:
            out.append(Line(ln.account, net, D(0), ln.note))
        elif net < 0:
            out.append(Line(ln.account, D(0), -net, ln.note))
    return out


JOURNAL_NAMESPACE = uuid.UUID("0b7f3a52-9c1e-4d55-8a3e-5f0c2d1e9a77")
JOURNAL_RANGE = ("journal", "BU-{YYYY}-{SEQ:6}")


def entry_id_for(source_type: str, source_id: UUID | None) -> UUID:
    """Deterministische Buchungs-ID je Vorgang: Wiederholungen übernehmen die bestehende Festschreibung (keine Lücken)."""
    return uuid.uuid5(JOURNAL_NAMESPACE, f"{source_type}:{source_id}") if source_id else uuid.uuid4()


async def posting_date(ledger: Any, tenant_id: UUID, document_date: dt.date) -> dt.date:
    """Buchungsdatum: Belegdatum, oder der 1. der nächsten offenen Periode, falls die Periode festgeschrieben ist."""
    locked = set(await ledger.locked_periods(tenant_id=tenant_id)) if ledger is not None else set()
    day = document_date
    while f"{day:%Y-%m}" in locked:
        day = (day.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return day


async def post(
    conn: AsyncConnection,
    ledger: Any,
    tenant_id: UUID,
    *,
    chart: str,
    entry_date: dt.date,
    description: str,
    document_ref: str | None,
    source_type: str,
    source_id: UUID | None,
    lines: list[Line],
    method: str,
    actor: str,
    ai_model: str | None = None,
    ai_reasoning: str | None = None,
    closing: bool = False,
) -> UUID:
    """Prüft, schreibt im Ledger fest (lückenlose Nummer, Periodensperre) und legt den Buchungssatz an.

    Reihenfolge: Festschreibung zuerst. Geht danach die Datenbanktransaktion verloren, findet die Wiederholung die
    bestehende Festschreibung über die deterministische ID und übernimmt deren (verbindlichen) Inhalt.
    """
    from app.services.invoices import today_berlin

    lines = _normalize(lines)
    if not lines:
        raise AccountingError("Buchungssatz ohne Beträge")
    if sum((ln.debit for ln in lines), D(0)) != sum((ln.credit for ln in lines), D(0)):
        raise AccountingError("Buchungssatz nicht ausgeglichen (Soll ≠ Haben)")
    known = {r[0] for r in await conn.execute(text("SELECT number FROM accounts WHERE chart = :c"), {"c": chart})}
    unknown = sorted({ln.account for ln in lines} - known)
    if unknown:
        raise AccountingError(f"Konto {', '.join(unknown)} existiert im {chart} nicht")
    document_date = entry_date
    if closing:
        # Abschlussbuchung: immer 31.12., Abschlussperiode JJJJ-13 (keine Verschiebung in das Folgejahr)
        if (entry_date.month, entry_date.day) != (12, 31):
            raise AccountingError("Abschlussbuchungen tragen das Datum 31.12.")
        booking_date = entry_date
        period = f"{entry_date.year}-13"
    else:
        booking_date = await posting_date(ledger, tenant_id, document_date)
        period = f"{booking_date:%Y-%m}"
    entry_id = entry_id_for(source_type, source_id)
    if booking_date != document_date:
        description = f"{description} (Beleg vom {document_date:%d.%m.%Y}, Periode festgeschrieben)"

    sealed = None
    if ledger is not None:
        records = await ledger.records_by_subject(tenant_id=tenant_id, subject_id=entry_id)
        existing = [r for r in records if r.record_type == "journal.posted"]
        if existing:
            sealed = existing[0]
            p = sealed.payload
            chart, description = p["chart"], p["description"]
            document_date, booking_date = dt.date.fromisoformat(p["document_date"]), dt.date.fromisoformat(p["entry_date"])
            lines = [Line(x["account"], D(x["debit"]), D(x["credit"]), x.get("note")) for x in p["lines"]]
        else:
            sealed = await ledger.seal(
                tenant_id=tenant_id, record_type="journal.posted", sealed_by=f"user:{actor}", subject_id=entry_id,
                number_range=JOURNAL_RANGE, issue_date=today_berlin(), posting_period=period,
                payload={"chart": chart, "document_date": document_date, "entry_date": booking_date, "description": description[:300],
                         "document_ref": document_ref, "source_type": source_type, **({"closing": True} if closing else {}),
                         "source_id": str(source_id) if source_id else None, "method": method,
                         "lines": [{"account": ln.account, "debit": ln.debit, "credit": ln.credit, "note": ln.note} for ln in lines]})
    params = {"id": entry_id, "t": tenant_id, "c": chart, "d": booking_date, "dd": document_date, "desc": description[:300],
              "ref": document_ref, "st": source_type, "sid": source_id, "m": method, "am": ai_model, "ar": ai_reasoning, "a": actor,
              "rid": sealed.id if sealed else None, "h": sealed.hash if sealed else None, "cl": closing}
    cols = "id, tenant_id, chart, entry_date, document_date, description, document_ref, source_type, source_id, method, ai_model, " \
        "ai_reasoning, created_by, ledger_record_id, ledger_hash, closing"
    vals = ":id, :t, :c, :d, :dd, :desc, :ref, :st, :sid, :m, :am, :ar, :a, :rid, :h, :cl"
    if sealed is not None and sealed.document_number:
        cols += ", number"
        vals += ", :num"
        params["num"] = sealed.document_number
    await conn.execute(text(f"INSERT INTO journal_entries ({cols}) VALUES ({vals})"), params)
    for pos, ln in enumerate(lines, start=1):
        await conn.execute(
            text("INSERT INTO journal_lines (entry_id, position, account, debit, credit, note) VALUES (:e, :p, :a, :d, :c, :n)"),
            {"e": entry_id, "p": pos, "a": ln.account, "d": ln.debit, "c": ln.credit, "n": ln.note},
        )
    return entry_id


# ───────────────────────── Regelbuchungen ─────────────────────────


def invoice_lines(chart: str, snapshot: dict[str, Any]) -> list[Line]:
    """Ausgangsrechnung: Forderung an Erlös (+ USt); Fremdwährung zum Kurs des Rechnungsdatums in EUR."""
    from app.domain.international import to_eur

    fx = snapshot.get("exchange_rate")
    lines: list[Line] = []
    total = D(0)
    zero_rated = {"K": "revenue_ig_goods", "AE": "revenue_eu_service", "O": "revenue_third_country"}
    for g in snapshot["tax_breakdown"]:
        net, tax = to_eur(g["taxable_amount"], fx), to_eur(g["tax_amount"], fx)
        if g["category"] in zero_rated:
            lines.append(Line(acc(chart, zero_rated[g["category"]]), credit=net))
            total += net
            continue
        rate = D(g["rate"]).normalize()
        suffix = "19" if rate == 19 else "7" if rate == 7 else None
        if suffix is None:
            raise AccountingError(f"Kein Erlöskonto für Steuersatz {g['rate']} %")
        lines.append(Line(acc(chart, f"revenue_{suffix}"), credit=net))
        if tax:
            lines.append(Line(acc(chart, f"output_vat_{suffix}"), credit=tax))
        total += net + tax
    return [Line(acc(chart, "receivables"), debit=total, note=snapshot["buyer"].get("name")), *lines]


def payment_lines(chart: str, p: dict[str, Any]) -> list[Line]:
    """Zahlungseingang; bei Fremdwährung: Forderung zum Rechnungskurs, Bank mit dem tatsächlichen EUR-Betrag, Rest = Kursdifferenz."""
    from app.domain.international import to_eur

    receivable = to_eur(p["amount"], p.get("exchange_rate"))
    bank = D(p["amount_eur"]) if p.get("amount_eur") else receivable
    lines = [Line(acc(chart, "bank"), debit=bank), Line(acc(chart, "receivables"), credit=receivable)]
    diff = bank - receivable
    if diff > 0:
        lines.append(Line(acc(chart, "fx_gain"), credit=diff, note="Kursgewinn"))
    elif diff < 0:
        lines.append(Line(acc(chart, "fx_loss"), debit=-diff, note="Kursverlust"))
    return lines


def receipt_tax_lines(chart: str, r: dict[str, Any], expense_lines: list[Line]) -> list[Line]:
    """Ergänzt zu den Aufwandszeilen (Summe = Aufwandsbasis) die Steuer- und Gegenbuchung."""
    case = r["tax_case"]
    gross, net = D(r["gross_amount"]), D(r["net_amount"])
    lines = list(expense_lines)
    if case in ("IN_DE_19", "IN_BEWIRTUNG", "IN_EINFUHR"):
        lines.append(Line(acc(chart, "input_vat_19"), debit=D(r["input_tax_amount"]), note="Vorsteuer 19 %"))
        payable = gross
    elif case == "IN_DE_7":
        lines.append(Line(acc(chart, "input_vat_7"), debit=D(r["input_tax_amount"]), note="Vorsteuer 7 %"))
        payable = gross
    elif case in ("IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB"):
        rc = D(r["rc_tax_amount"])
        lines.append(Line(acc(chart, "rc_input_vat_19"), debit=rc, note="Vorsteuer § 13b"))
        lines.append(Line(acc(chart, "rc_output_vat_19"), credit=rc, note="Umsatzsteuer § 13b"))
        payable = net
    else:  # ohne Vorsteuerabzug
        payable = gross
    lines.append(Line(acc(chart, "payables"), credit=payable, note=r.get("supplier_name")))
    return lines


def expense_base(r: dict[str, Any]) -> Decimal:
    """Betrag, der auf Aufwands-/Anlagekonten verteilt wird."""
    if r["tax_case"] in ("IN_OHNE_VST", "IN_NICHT_ABZIEHBAR"):
        return D(r["gross_amount"])
    return D(r["net_amount"])


def default_expense_lines(chart: str, r: dict[str, Any], account: str | None = None) -> list[Line]:
    base = expense_base(r)
    if r["tax_case"] == "IN_BEWIRTUNG" or r["category"] == "hospitality":
        part = (base * BEWIRTUNG_DEDUCTIBLE).quantize(CENT)
        return [
            Line(account or acc(chart, "hospitality"), debit=part, note="Bewirtung 70 %"),
            Line(acc(chart, "hospitality_nd"), debit=base - part, note="Bewirtung 30 % (nicht abzugsfähig)"),
        ]
    return [Line(account or category_account(chart, r["category"]), debit=base)]


async def ensure_postings(conn: AsyncConnection, ledger: Any, tenant_id: UUID, actor: str = "system:buchhaltung") -> int:
    """Erzeugt fehlende Regelbuchungen (idempotent) für Rechnungen, Zahlungen und gebuchte Belege."""
    chart = await chart_of(conn, tenant_id)
    n = 0
    invoices = (
        (
            await conn.execute(
                text(
                    "SELECT i.id, i.number, i.kind, i.issue_date, i.snapshot FROM invoices i WHERE i.tenant_id = :t AND i.status = "
                    "'issued' "
                    "AND NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.source_type = 'invoice' AND j.source_id = i.id) ORDER BY "
                    "i.issue_date"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for inv in invoices:
        await post(
            conn,
            ledger,
            tenant_id,
            chart=chart,
            entry_date=inv["issue_date"],
            document_ref=inv["number"],
            description=f"{'Storno ' if inv['kind'] == 'cancellation' else ''}Ausgangsrechnung {inv['number']}",
            source_type="invoice",
            source_id=inv["id"],
            lines=invoice_lines(chart, inv["snapshot"]),
            method="rule",
            actor=actor,
        )
        n += 1
    payments = (
        (
            await conn.execute(
                text(
                    "SELECT p.id, p.paid_on, p.amount, p.amount_eur, p.reversed_at, i.number, i.exchange_rate FROM payments p "
                    "JOIN invoices i ON i.id = p.invoice_id "
                    "WHERE p.tenant_id = :t AND NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.source_type = 'payment' AND "
                    "j.source_id = p.id) "
                    "ORDER BY p.paid_on"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for p in payments:
        await post(
            conn,
            ledger,
            tenant_id,
            chart=chart,
            entry_date=p["paid_on"],
            document_ref=p["number"],
            description=f"Zahlung {p['number']}",
            source_type="payment",
            source_id=p["id"],
            method="rule",
            actor=actor,
            lines=payment_lines(chart, dict(p)),
        )
        n += 1
    from app.services.invoices import BERLIN

    reversals = (
        (
            await conn.execute(
                text(
                    "SELECT p.id, p.reversed_at, p.amount, p.amount_eur, i.number, i.exchange_rate FROM payments p "
                    "JOIN invoices i ON i.id = p.invoice_id WHERE "
                    "p.tenant_id = :t "
                    "AND p.reversed_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.source_type = "
                    "'payment_reversal' "
                    "AND j.source_id = p.id)"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for p in reversals:
        await post(
            conn,
            ledger,
            tenant_id,
            chart=chart,
            entry_date=p["reversed_at"].astimezone(BERLIN).date(),
            document_ref=p["number"],
            description=f"Rückbuchung Zahlung {p['number']}",
            source_type="payment_reversal",
            source_id=p["id"],
            method="rule",
            actor=actor,
            lines=[Line(ln.account, debit=ln.credit, credit=ln.debit, note=ln.note) for ln in payment_lines(chart, dict(p))],
        )
        n += 1
    receipts = (
        (
            await conn.execute(
                text(
                    "SELECT r.*, s.name AS supplier_name FROM receipts r JOIN suppliers s ON s.id = r.supplier_id WHERE r.tenant_id = :t "
                    "AND r.status = 'booked' AND NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.source_type = 'receipt' AND "
                    "j.source_id = r.id) "
                    "ORDER BY r.receipt_date"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for r in receipts:
        rd = dict(r)
        await post(
            conn,
            ledger,
            tenant_id,
            chart=chart,
            entry_date=rd["receipt_date"],
            document_ref=rd["invoice_number"],
            description=f"{'Storno ' if rd['kind'] == 'reversal' else ''}{rd['supplier_name']}: {rd['description']}",
            source_type="receipt",
            source_id=rd["id"],
            method="rule",
            actor=actor,
            lines=receipt_tax_lines(chart, rd, default_expense_lines(chart, rd, rd.get("expense_account"))),
        )
        n += 1
    rpaid = (
        (
            await conn.execute(
                text(
                    "SELECT r.id, r.paid_on, r.gross_amount, r.net_amount, r.tax_case, r.invoice_number, r.description FROM receipts r "
                    "WHERE r.tenant_id = :t AND r.status = 'booked' AND r.kind = 'expense' AND r.paid_on IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.source_type = 'receipt_payment' AND j.source_id = r.id)"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for r in rpaid:
        amount = (
            D(r["net_amount"])
            if r["tax_case"] in ("IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB")
            else D(r["gross_amount"])
        )
        await post(
            conn,
            ledger,
            tenant_id,
            chart=chart,
            entry_date=r["paid_on"],
            document_ref=r["invoice_number"],
            description=f"Zahlung {r['description']}",
            source_type="receipt_payment",
            source_id=r["id"],
            method="rule",
            actor=actor,
            lines=[Line(acc(chart, "payables"), debit=amount), Line(acc(chart, "bank"), credit=amount)],
        )
        n += 1
    return n


# ───────────────────────── Claude-Kontierung ─────────────────────────

ASSIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Kontonummer aus der vorgegebenen Liste"},
                    "amount": {"type": "string", "description": "Betrag mit Punkt, 2 Nachkommastellen"},
                    "note": {"type": "string", "description": "kurzer Buchungstext"},
                },
                "required": ["account", "amount", "note"],
                "additionalProperties": False,
            },
        },
        "category": {"type": "string", "enum": [c.code for c in CATEGORIES]},
        "booking_text": {"type": "string", "description": "Buchungstext, max. 60 Zeichen"},
        "reasoning": {"type": "string", "description": "Begründung der Kontierung in 1–3 Sätzen (deutsch)"},
        "confidence": {"type": "number"},
    },
    "required": ["lines", "category", "booking_text", "reasoning", "confidence"],
    "additionalProperties": False,
}

ASSIGN_SYSTEM = (
    "Du bist Bilanzbuchhalter einer deutschen UG (haftungsbeschränkt) und kontierst Eingangsbelege im Kontenrahmen {chart}. "
    "Verteile den vorgegebenen Betrag vollständig auf die passenden Aufwands- oder Anlagekonten aus der Liste (Summe exakt "
    "gleich dem Betrag). Teile nur auf, wenn der Beleg verschiedene Leistungsarten enthält. Wirtschaftsgüter über 800 € netto "
    "sind Anlagevermögen (Anlagekonto), bis 800 € netto sofort abschreibbare GWG. Vorsteuer und Gegenkonto bucht das System "
    "selbst – nur Aufwands-/Anlagekonten angeben. In der Regel genau EINE Zeile mit dem gesamten Betrag. "
    "Begründe kurz und sachlich."
)


def ai_accounts(all_accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Konten, die die KI für Eingangsbelege vorschlagen darf: Aufwand und Anlagevermögen (Kontenklasse 0).

    Bestandskonten wie Bank, Kasse, Forderungen oder Vorsteuer sind ausgeschlossen – Vorsteuer und Gegenbuchung
    setzt das System selbst nach festen Regeln.
    """
    return [a for a in all_accounts if a["type"] == "expense" or (a["type"] == "asset" and str(a["number"]).startswith("0"))]


def assign_schema(allowed_numbers: list[str]) -> dict[str, Any]:
    """Schema je Beleg: nur erlaubte Kontonummern, Beträge im Format 123.45, höchstens drei Zeilen.

    Die Einschränkung verhindert, dass kleinere lokale Modelle endlos Zeilen wiederholen oder Konten erfinden.
    """
    schema = copy.deepcopy(ASSIGN_SCHEMA)
    lines = schema["properties"]["lines"]
    lines["minItems"], lines["maxItems"] = 1, 3
    props = lines["items"]["properties"]
    props["account"]["enum"] = allowed_numbers
    props["amount"]["pattern"] = r"^[0-9]{1,9}\.[0-9]{2}$"
    props["note"]["maxLength"] = 120
    schema["properties"]["booking_text"]["maxLength"] = 60
    schema["properties"]["reasoning"]["maxLength"] = 300
    return schema


async def ai_assign(provider: Any, *, chart: str, receipt: dict[str, Any], allowed: list[dict[str, Any]],
                    file: tuple[bytes, str] | None) -> tuple[dict[str, Any], str]:
    from app.services.ai import AIError

    base = expense_base(receipt)
    listing = "\n".join(f"{a['number']} {a['name']} ({'Anlage' if a['type'] == 'asset' else 'Aufwand'})" for a in allowed)
    names = {str(a["number"]): a["name"] for a in allowed}
    # Hinweis nur bei einer echten Kategorie – bei „Sonstiges“ würde er kleinere Modelle auf das Sammelkonto festlegen
    usual_hint = ""
    if receipt.get("category") and receipt["category"] != "other":
        number = category_account(chart, receipt["category"])
        usual_hint = f"Übliches Konto für diese Kategorie: {number} {names.get(number, '')} – nimm es, wenn es zur Leistung passt.\n"
    category_text = (usual_hint and receipt["category"]) or "offen – bitte anhand der Leistung selbst bestimmen"
    prompt = (
        f"Beleg: Lieferant {receipt['supplier_name']}, Rechnung {receipt.get('invoice_number') or '–'} vom {receipt['receipt_date']}, "
        f"Beschreibung „{receipt['description']}“, Kategorie {category_text}, Steuerfall {receipt['tax_case']}.\n"
        f"Zu verteilender Betrag: {base} EUR.\n"
        f"{usual_hint}\nErlaubte Konten ({chart}):\n{listing}"
    )
    try:
        result = await provider.complete_json(system=ASSIGN_SYSTEM.format(chart=chart), text=prompt, file=file,
                                              schema=assign_schema([str(a["number"]) for a in allowed]))
    except AIError as exc:
        raise AccountingError(str(exc)) from exc
    return result, str(provider.label)


def validate_assignment(result: dict[str, Any], *, base: Decimal, allowed: set[str]) -> list[Line]:
    raw = list(result.get("lines", []))
    if len(raw) == 1:
        # Eine Zeile → Betrag steht fest (gesamter Aufwand); die KI wählt nur das Konto
        raw = [{**raw[0], "amount": str(base)}]
    lines = []
    for ln in raw:
        account = str(ln.get("account", "")).strip()
        if account not in allowed:
            raise AccountingError(f"Die KI schlug Konto {account} vor, das nicht zulässig ist")
        try:
            amount = D(str(ln["amount"])).quantize(CENT)
        except Exception as exc:
            raise AccountingError("Betrag der KI nicht lesbar") from exc
        if amount <= 0 and base > 0:
            raise AccountingError("Beträge der KI müssen positiv sein")
        lines.append(Line(account, debit=amount, note=str(ln.get("note") or "")[:120] or None))
    if not lines:
        raise AccountingError("Die KI hat keine Kontierung geliefert")
    if sum((ln.debit for ln in lines), D(0)) != base:
        raise AccountingError(f"Kontierung der KI ergibt nicht exakt {base} €")
    return lines


async def book_receipt_with_ai(
    conn: AsyncConnection, ledger: Any, provider: Any, tenant_id: UUID, receipt_id: UUID, actor: str
) -> dict[str, Any]:
    """Ein Klick: KI kontiert → Prüfung → Beleg festschreiben → Buchungssatz (mit Begründung) schreiben."""
    from app.services import receipts as rc

    r = await rc.get_receipt(conn, tenant_id, receipt_id, lock=True)
    if r["status"] != "draft":
        raise AccountingError("Beleg ist bereits gebucht")
    chart = await chart_of(conn, tenant_id)
    allowed = ai_accounts(await accounts(conn, chart))
    file = None
    if r["file_id"]:
        f = await rc.get_file(conn, tenant_id, r["file_id"])
        file = (bytes(f["content"]), f["mime_type"])
    rd = dict(r)
    numbers = {a["number"] for a in allowed}
    try:
        result, used_model = await ai_assign(provider, chart=chart, receipt=rd, allowed=allowed, file=file)
    except AccountingError:
        if file is None:
            raise
        result = {}  # unbrauchbare Antwort (z. B. abgeschnitten) → zweiter Versuch unten
    if file is not None and result.get("category") != "hospitality":
        try:
            validate_assignment(result, base=expense_base(rd), allowed=numbers)
        except AccountingError:
            # Zweiter Versuch nur mit den erfassten Belegdaten: kleinere lokale Modelle lassen sich vom Dokumenttext
            # gelegentlich ablenken. Auch dieses Ergebnis wird unten vollständig geprüft.
            result, used_model = await ai_assign(provider, chart=chart, receipt=rd, allowed=allowed, file=None)
    hospitality = rd["tax_case"] == "IN_BEWIRTUNG" or result.get("category") == "hospitality"
    if result.get("category") and result["category"] != rd["category"] and not hospitality:
        await rc.save_draft(
            conn,
            tenant_id,
            {
                k: rd[k]
                for k in (
                    "supplier_id",
                    "file_id",
                    "receipt_date",
                    "service_date",
                    "invoice_number",
                    "description",
                    "tax_case",
                    "net_amount",
                    "tax_amount",
                    "paid_on",
                )
            }
            | {"category": result["category"]},
            actor,
            receipt_id,
        )
        rd = dict(await rc.get_receipt(conn, tenant_id, receipt_id))
    base = expense_base(rd)
    expense = (
        default_expense_lines(chart, rd) if hospitality else validate_assignment(result, base=base, allowed={a["number"] for a in allowed})
    )
    booked = await rc.book_receipt(conn, ledger, tenant_id, receipt_id, actor)
    rd = dict(await rc.get_receipt(conn, tenant_id, receipt_id)) | {"supplier_name": rd["supplier_name"]}
    entry_id = await post(
        conn,
        ledger,
        tenant_id,
        chart=chart,
        entry_date=rd["receipt_date"],
        document_ref=rd["invoice_number"],
        description=str(result.get("booking_text") or f"{rd['supplier_name']}: {rd['description']}")[:300],
        source_type="receipt",
        source_id=receipt_id,
        lines=receipt_tax_lines(chart, rd, expense),
        method="ai",
        actor=actor,
        ai_model=used_model,
        ai_reasoning=f"{result.get('reasoning', '')} (Sicherheit {result.get('confidence', '–')})"[:2000],
    )
    await audit.record(
        conn,
        tenant_id=tenant_id,
        actor=actor,
        action="receipt.ai_booked",
        entity_type="receipt",
        entity_id=receipt_id,
        after={"entry_id": str(entry_id), "model": used_model},
    )
    return {"receipt": booked, "entry_id": entry_id, "reasoning": result.get("reasoning"), "lines": len(expense)}


# ───────────────────────── Auswertungen ─────────────────────────


async def journal(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    entries = (
        (
            await conn.execute(
                text(
                    "SELECT id, number, chart, entry_date, document_date, ledger_hash, created_at, closing, description, document_ref, "
                    "source_type, "
                    "source_id, method, ai_model, "
                    "ai_reasoning, created_by "
                    "FROM journal_entries WHERE tenant_id = :t AND entry_date BETWEEN :s AND :e ORDER BY entry_date, number"
                ),
                {"t": tenant_id, "s": start, "e": end},
            )
        )
        .mappings()
        .all()
    )
    out = []
    for e in entries:
        lines = (
            (
                await conn.execute(
                    text(
                        "SELECT l.account, l.debit, l.credit, l.note, a.name FROM journal_lines l LEFT JOIN accounts a ON a.chart = :c "
                        "AND a.number = l.account "
                        "WHERE l.entry_id = :id ORDER BY l.position"
                    ),
                    {"id": e["id"], "c": e["chart"]},
                )
            )
            .mappings()
            .all()
        )
        out.append(dict(e) | {"lines": [dict(ln) for ln in lines]})
    return out


async def balances(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date) -> dict[str, Any]:
    chart = await chart_of(conn, tenant_id)
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT l.account, a.name, a.type, SUM(l.debit) AS debit, SUM(l.credit) AS credit FROM journal_lines l "
                    "JOIN journal_entries j ON j.id = l.entry_id LEFT JOIN accounts a ON a.chart = j.chart AND a.number = l.account "
                    "WHERE j.tenant_id = :t AND j.entry_date BETWEEN :s AND :e GROUP BY l.account, a.name, a.type ORDER BY l.account"
                ),
                {"t": tenant_id, "s": start, "e": end},
            )
        )
        .mappings()
        .all()
    )
    accts = [dict(r) | {"balance": D(r["debit"]) - D(r["credit"])} for r in rows]
    revenue = -sum((a["balance"] for a in accts if a["type"] == "revenue"), D(0))
    expenses = sum((a["balance"] for a in accts if a["type"] == "expense"), D(0))
    total_debit = sum((D(a["debit"]) for a in accts), D(0))
    total_credit = sum((D(a["credit"]) for a in accts), D(0))
    return {
        "chart": chart,
        "accounts": accts,
        "revenue": revenue,
        "expenses": expenses,
        "result": revenue - expenses,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }
