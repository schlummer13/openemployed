"""Umstieg aus anderer Software: Eröffnungsbilanz, offene Posten, DATEV-Buchungsstapel und Stammdaten-CSV.

Alle Importe laufen zweistufig: Vorschau (``dry_run``) mit Fehlerliste, danach Übernahme per Klick. Jede Datei wird über
ihren SHA-256 protokolliert und kann nicht versehentlich doppelt übernommen werden; Buchungen bekommen deterministische IDs.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
ZERO = D("0.00")
IMPORT_NS = uuid.UUID("2c7b9e14-6a0d-4f8e-b3a1-9d5e0c7f4b62")
MAX_ROWS = 20000


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def decode(content: bytes) -> str:
    """CSV-Dateien aus DATEV/Lexoffice/sevDesk: UTF-8 (mit/ohne BOM) oder Windows-1252."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ConflictError("Datei ist weder UTF-8 noch Windows-1252 kodiert")


def parse_amount(value: str) -> D:
    v = value.strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not v:
        raise ValueError("Betrag fehlt")
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return D(v).quantize(D("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"Betrag {value!r} ungültig") from exc


def _strp(value: str, fmt: str) -> dt.date:
    return dt.datetime.strptime(value, fmt).date()  # noqa: DTZ007 – reine Kalenderdaten ohne Uhrzeit


def parse_date(value: str, *, year: int | None = None) -> dt.date:
    v = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return _strp(v, fmt)
        except ValueError:
            continue
    if year and re.fullmatch(r"\d{3,4}", v):  # DATEV-Belegdatum TTMM
        v = v.zfill(4)
        return dt.date(year, int(v[2:]), int(v[:2]))
    raise ValueError(f"Datum {value!r} ungültig")


async def _record_import(conn: AsyncConnection, tenant_id: UUID, kind: str, filename: str | None, sha: str, rows: int,
                         summary: dict[str, Any], actor: str) -> None:
    dup = await conn.scalar(text("SELECT created_at FROM data_imports WHERE tenant_id = :t AND kind = :k AND sha256 = :s"),
                            {"t": tenant_id, "k": kind, "s": sha})
    if dup:
        raise ConflictError(f"Diese Datei wurde bereits am {dup:%d.%m.%Y} importiert")
    await conn.execute(text(
        "INSERT INTO data_imports (tenant_id, kind, filename, sha256, rows, summary, created_by) "
        "VALUES (:t, :k, :f, :s, :r, CAST(:sum AS jsonb), :a)"),
        {"t": tenant_id, "k": kind, "f": (filename or "")[:200] or None, "s": sha, "r": rows,
         "sum": json.dumps(summary, default=str), "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"import.{kind}", entity_type="data_import",
                       entity_id=sha[:16], after={"filename": filename, "rows": rows, **summary})


async def history(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, kind, filename, rows, summary, created_by, created_at FROM data_imports WHERE tenant_id = :t "
        "ORDER BY created_at DESC LIMIT 50"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


# ───────────────────────── Eröffnungsbilanz ─────────────────────────

def account_type_guess(chart: str, number: str) -> str:
    """Kontenart nach Kontenklasse (für unbekannte Konten aus Importen)."""
    n = int(number)
    if chart == "SKR04":
        cls = n // 1000
        return {0: "asset", 1: "asset", 2: "equity", 3: "liability", 4: "revenue", 9: "equity"}.get(cls, "expense")
    if 800 <= n < 900 or n >= 9000:
        return "equity"
    if 900 <= n < 1000 or 1600 <= n < 1800:
        return "liability"
    if n < 1600 or 7000 <= n < 8000:
        return "asset"
    if 8000 <= n < 9000 or 2700 <= n < 2800:
        return "revenue"
    return "expense"


async def ensure_accounts(conn: AsyncConnection, chart: str, numbers: set[str]) -> list[str]:
    known = {r[0] for r in await conn.execute(text("SELECT number FROM accounts WHERE chart = :c"), {"c": chart})}
    created = sorted(numbers - known)
    for n in created:
        await conn.execute(text("INSERT INTO accounts (chart, number, name, type) VALUES (:c, :n, :name, :t) ON CONFLICT DO NOTHING"),
                           {"c": chart, "n": n, "name": f"Konto {n} (Import)", "t": account_type_guess(chart, n)})
    return created


async def opening_state(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    from app.services.accounting import accounts, chart_of

    chart = await chart_of(conn, tenant_id)
    entry = (await conn.execute(text(
        "SELECT id, entry_date, number, description FROM journal_entries WHERE tenant_id = :t AND source_type = 'opening' "
        "ORDER BY entry_date LIMIT 1"), {"t": tenant_id})).mappings().one_or_none()
    lines = []
    if entry:
        lines = [dict(r) for r in (await conn.execute(text(
            "SELECT account, debit, credit, note FROM journal_lines WHERE entry_id = :e ORDER BY position"),
            {"e": entry["id"]})).mappings()]
    items = [dict(r) for r in (await conn.execute(text(
        "SELECT id, kind, party, document_number, document_date, due_date, amount, paid_on FROM opening_items WHERE tenant_id = :t "
        "ORDER BY kind, document_date"), {"t": tenant_id})).mappings()]
    accs = [a for a in await accounts(conn, chart)
            if a["type"] in ("asset", "liability", "equity") and not str(a["number"]).startswith("900")]
    return {"chart": chart, "entry": dict(entry) if entry else None, "lines": lines, "items": items, "accounts": accs}


async def post_opening(conn: AsyncConnection, ledger: Any, tenant_id: UUID, *, opening_date: dt.date, balances: list[dict[str, Any]],
                       items: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    """Bucht die Eröffnungsbilanz einmalig: Bestandskonten gegen 9000, offene Posten gegen 9008/9009."""
    from app.services.accounting import Line, acc, chart_of, post

    if await conn.scalar(text("SELECT 1 FROM journal_entries WHERE tenant_id = :t AND source_type = 'opening'"), {"t": tenant_id}):
        raise ConflictError("Die Eröffnungsbilanz ist bereits gebucht – Korrekturen bitte als manuelle Buchung")
    chart = await chart_of(conn, tenant_id)
    lines: list[Line] = []
    for b in balances:
        debit, credit = D(b.get("debit") or 0), D(b.get("credit") or 0)
        if debit and credit:
            raise ConflictError(f"Konto {b['account']}: entweder Soll oder Haben")
        if debit or credit:
            lines.append(Line(b["account"], debit=debit, credit=credit, note="Eröffnungsbilanz"))
            lines.append(Line("9000", debit=credit, credit=debit))
    recv = sum((D(i["amount"]) for i in items if i["kind"] == "receivable"), ZERO)
    pay = sum((D(i["amount"]) for i in items if i["kind"] == "payable"), ZERO)
    if recv:
        lines += [Line(acc(chart, "receivables"), debit=recv, note="offene Forderungen"), Line("9008", credit=recv)]
    if pay:
        lines += [Line("9009", debit=pay), Line(acc(chart, "payables"), credit=pay, note="offene Verbindlichkeiten")]
    if not lines:
        raise ConflictError("Keine Salden erfasst")
    # Bilanzgleichheit: Summe der Saldenvortragskonten muss null ergeben (Aktiva = Passiva)
    vortrag = sum((ln.debit - ln.credit for ln in lines if ln.account in ("9000", "9008", "9009")), ZERO)
    if vortrag != 0:
        raise ConflictError(f"Eröffnungsbilanz nicht ausgeglichen: Differenz {abs(vortrag):.2f} € "
                            f"({'Aktiva' if vortrag < 0 else 'Passiva'} zu hoch)")
    await ensure_accounts(conn, chart, {ln.account for ln in lines})
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=opening_date, description=f"Eröffnungsbilanz "
                                                                                                  f"{opening_date:%d.%m.%Y}",
                       document_ref="EB", source_type="opening", source_id=uuid.uuid5(IMPORT_NS, f"opening:{tenant_id}"),
                       lines=lines, method="manual", actor=actor)
    for i in items:
        try:
            await conn.execute(text(
                "INSERT INTO opening_items (tenant_id, kind, party, document_number, document_date, due_date, amount, journal_entry_id, "
                "created_by) VALUES (:t, :kind, :party, :document_number, :document_date, :due_date, :amount, :e, :a)"),
                {**i, "t": tenant_id, "e": entry, "a": actor})
        except Exception as exc:  # doppelte Belegnummer
            raise ConflictError(f"Offener Posten {i['document_number']} doppelt") from exc
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="opening.posted", entity_type="journal_entry", entity_id=entry,
                       after={"date": str(opening_date), "accounts": len(balances), "items": len(items)})
    return await opening_state(conn, tenant_id)


async def pay_item(conn: AsyncConnection, ledger: Any, tenant_id: UUID, item_id: UUID, paid_on: dt.date, actor: str) -> None:
    from app.services.accounting import Line, acc, chart_of, post

    item = (await conn.execute(text("SELECT * FROM opening_items WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                               {"t": tenant_id, "id": item_id})).mappings().one_or_none()
    if item is None:
        raise NotFoundError("Offener Posten nicht gefunden")
    if item["paid_on"]:
        raise ConflictError("Bereits als bezahlt gebucht")
    chart = await chart_of(conn, tenant_id)
    amt = D(item["amount"])
    lines = ([Line(acc(chart, "bank"), debit=amt), Line(acc(chart, "receivables"), credit=amt)] if item["kind"] == "receivable"
             else [Line(acc(chart, "payables"), debit=amt), Line(acc(chart, "bank"), credit=amt)])
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=paid_on,
                       description=f"{'Zahlungseingang' if item['kind'] == 'receivable' else 'Zahlung'} {item['document_number']} "
                                   f"{item['party']}"[:300], document_ref=item["document_number"][:36], source_type="import",
                       source_id=uuid.uuid5(IMPORT_NS, f"opening-pay:{item_id}"), lines=lines, method="manual", actor=actor)
    await conn.execute(text("UPDATE opening_items SET paid_on = :d, payment_entry_id = :e WHERE id = :id"),
                       {"d": paid_on, "e": entry, "id": item_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="opening.item_paid", entity_type="opening_item",
                       entity_id=item_id, after={"paid_on": str(paid_on)})


def parse_items_csv(content: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Offene Posten: Art;Name;Belegnummer;Belegdatum;Fällig;Betrag (Art = Forderung/Verbindlichkeit)."""
    rows, errors = _read_csv(decode(content))
    out = []
    for i, r in enumerate(rows, start=2):
        try:
            kind_raw = _pick(r, "art", "typ", "kind").lower()
            kind = "payable" if kind_raw.startswith(("verb", "kred", "pay", "einkauf")) else "receivable"
            item: dict[str, Any] = {"kind": kind, "party": _pick(r, "name", "kunde", "lieferant", "partner")[:200] or "–",
                        "document_number": _pick(r, "belegnummer", "rechnungsnummer", "belegnr", "re-nr")[:100],
                        "document_date": parse_date(_pick(r, "belegdatum", "rechnungsdatum", "datum")),
                        "due_date": parse_date(d) if (d := _pick(r, "fällig", "faellig", "fälligkeit", "due")) else None,
                        "amount": parse_amount(_pick(r, "betrag", "offen", "brutto", "amount"))}
            if not item["document_number"]:
                raise ValueError("Belegnummer fehlt")
            out.append(item)
        except ValueError as exc:
            errors.append(f"Zeile {i}: {exc}")
    return out, errors


# ───────────────────────── CSV-Helfer ─────────────────────────

def _read_csv(textdata: str) -> tuple[list[dict[str, str]], list[str]]:
    sample = textdata[:5000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(textdata), delimiter=delim)
    rows: list[dict[str, str]] = []
    for n, r in enumerate(reader):
        if n >= MAX_ROWS:
            return rows, [f"Mehr als {MAX_ROWS} Zeilen – Datei bitte aufteilen"]
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in r.items() if k})
    return rows, []


def _pick(row: dict[str, str], *names: str) -> str:
    for n in names:
        if row.get(n):
            return row[n]
    for key, v in row.items():  # Teiltreffer am Wortanfang, z. B. „E-Mail-Adresse“ – nicht „Belegdatum“ für „Beleg“
        if v and any(key.startswith(n) and len(n) >= 4 for n in names):
            return v
    return ""


# ───────────────────────── Kunden und Lieferanten ─────────────────────────

def parse_parties(content: bytes, kind: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Lexoffice-, sevDesk- oder eigene CSV (Spaltennamen werden erkannt)."""
    rows, errors = _read_csv(decode(content))
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows, start=2):
        company = _pick(r, "firmenname", "firma", "unternehmen", "organisation", "company")
        first, last = _pick(r, "vorname", "first name"), _pick(r, "nachname", "last name")
        name = _pick(r, "name", "bezeichnung") if not (company or first or last) else ""
        if not (company or last or name):
            errors.append(f"Zeile {i}: Name fehlt")
            continue
        street = _pick(r, "straße", "strasse", "street", "adresse")
        nr = _pick(r, "hausnummer")
        out.append({
            "row": i, "company_name": company or name or None, "first_name": first or None, "last_name": last or None,
            "email": _pick(r, "e-mail", "email", "mail").lower() or None, "phone": _pick(r, "telefon", "phone", "tel") or None,
            "street": f"{street} {nr}".strip() or None, "postal_code": _pick(r, "plz", "postleitzahl", "zip") or None,
            "city": _pick(r, "ort", "stadt", "city") or None, "country": (_pick(r, "land", "country") or "DE")[:2].upper(),
            "vat_id": _pick(r, "ust-idnr", "ust-id", "umsatzsteuer-id", "vat").replace(" ", "").upper() or None,
            "number": _pick(r, "kundennummer", "lieferantennummer", "nummer") or None,
        })
    return out, errors


async def import_parties(conn: AsyncConnection, tenant_id: UUID, content: bytes, filename: str | None, kind: str, actor: str, *,
                         dry_run: bool) -> dict[str, Any]:
    from pydantic import ValidationError

    from app.api.core_schemas import CustomerIn
    from app.services.masterdata import create_customer
    from app.services.receipts import save_supplier

    parties, errors = parse_parties(content, kind)
    table = "customers" if kind == "customers" else "suppliers"
    name_col = "COALESCE(company_name, trim(coalesce(first_name,'') || ' ' || coalesce(last_name,'')))" if kind == "customers" else "name"
    existing = {r[0].lower() for r in await conn.execute(text(f"SELECT {name_col} FROM {table} WHERE tenant_id = :t"), {"t": tenant_id})
                if r[0]}
    ready: list[dict[str, Any]] = []
    skipped = 0
    for p in parties:
        display = p["company_name"] or f"{p['first_name'] or ''} {p['last_name'] or ''}".strip()
        if display.lower() in existing:
            skipped += 1
            continue
        if kind == "customers":
            try:
                payload = CustomerIn.model_validate({
                    "customer_type": "b2b" if p["company_name"] else "b2c", "company_name": p["company_name"],
                    "first_name": p["first_name"], "last_name": p["last_name"] or (None if p["company_name"] else display),
                    "email": p["email"] or "", "phone": p["phone"], "street": p["street"] or "", "postal_code": p["postal_code"] or "",
                    "city": p["city"] or "", "vat_id": p["vat_id"],
                    "notes": f"Importiert aus {filename or 'CSV'}" + (f", bisherige Nr. {p['number']}" if p["number"] else "")})
            except ValidationError as exc:
                msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])
                errors.append(f"Zeile {p['row']} ({display}): {msg}")
                continue
            ready.append({"display": display, "payload": payload})
        else:
            supplier = {"name": display[:200], "country_code": p["country"] if len(p["country"]) == 2 else "DE", "vat_id": p["vat_id"],
                       "default_tax_case": "IN_DE_19", "default_category": "other", "default_account": None,
                       "notes": f"Importiert aus {filename or 'CSV'}"}
            ready.append({"display": display, "payload": supplier})
        existing.add(display.lower())
    summary = {"new": len(ready), "skipped_existing": skipped, "errors": len(errors)}
    if not dry_run:
        if not ready:
            raise ConflictError("Nichts zu importieren")
        for r in ready:
            if kind == "customers":
                await create_customer(conn, tenant_id, r["payload"], actor)
            else:
                await save_supplier(conn, tenant_id, r["payload"], actor, needs_review=True)
        await _record_import(conn, tenant_id, kind, filename, _sha(content), len(ready), summary, actor)
    return {"summary": summary, "preview": [r["display"] for r in ready[:50]], "errors": errors[:100]}


# ───────────────────────── DATEV-Buchungsstapel (EXTF) ─────────────────────────

BU_TAX = {"9": ("input", D("19")), "8": ("input", D("7")), "3": ("output", D("19")), "2": ("output", D("7"))}


def parse_datev(content: bytes) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """DATEV-Format „Buchungsstapel“ (EXTF, Kategorie 21): Kopfzeile, Spaltenzeile, Buchungszeilen."""
    lines = decode(content).splitlines()
    if len(lines) < 3 or not lines[0].lstrip('"').startswith(("EXTF", "DTVF")):
        raise ConflictError("Keine DATEV-Datei (Kopfzeile EXTF fehlt) – Export „Buchungsstapel“ im DATEV-Format wählen")
    head = next(csv.reader([lines[0]], delimiter=";"))
    if len(head) > 3 and head[2] != "21":
        raise ConflictError(f"Datenkategorie {head[2]} ist kein Buchungsstapel (21)")
    meta: dict[str, Any] = {"format": head[0], "account_length": int(head[13]) if len(head) > 13 and head[13].isdigit() else 4}
    try:
        meta["fiscal_year_start"] = _strp(head[12], "%Y%m%d")
        meta["period_from"] = _strp(head[14], "%Y%m%d")
        meta["period_to"] = _strp(head[15], "%Y%m%d")
    except (IndexError, ValueError):
        meta["fiscal_year_start"] = None
    year = (meta.get("period_from") or meta.get("fiscal_year_start") or dt.datetime.now(dt.UTC).date()).year
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])), delimiter=";")
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, r in enumerate(reader, start=3):
        if n - 3 >= MAX_ROWS:
            errors.append(f"Mehr als {MAX_ROWS} Buchungen – Stapel bitte aufteilen")
            break
        try:
            amount = parse_amount(r.get("Umsatz (ohne Soll/Haben-Kz)", "") or r.get("Umsatz", ""))
            sh = (r.get("Soll/Haben-Kennzeichen") or "S").strip().upper()
            konto, gegen = (r.get("Konto") or "").strip(), (r.get("Gegenkonto (ohne BU-Schlüssel)") or r.get("Gegenkonto") or "").strip()
            if not konto or not gegen:
                raise ValueError("Konto oder Gegenkonto fehlt")
            date = parse_date(r.get("Belegdatum") or "", year=year)
            if meta.get("period_from") and date < meta["period_from"]:
                date = date.replace(year=date.year + 1)  # TTMM über den Jahreswechsel
            rows.append({"line": n, "amount": amount, "debit_side": sh == "S", "account": konto, "contra": gegen,
                         "bu": (r.get("BU-Schlüssel") or "").strip(), "date": date,
                         "document": (r.get("Belegfeld 1") or "").strip()[:36] or None,
                         "text": (r.get("Buchungstext") or "").strip()[:60]})
        except (ValueError, KeyError) as exc:
            errors.append(f"Zeile {n}: {exc}")
    return meta, rows, errors


def _map_account(chart: str, number: str, length: int) -> tuple[str, str | None]:
    """Personenkonten (Debitoren/Kreditoren) → Sammelkonto; Rückgabe (Konto, Hinweis)."""
    from app.services.accounting import acc

    digits = number.lstrip("0") or "0"
    if len(number) > length or int(digits) >= 10 ** length:
        n = int(digits)
        if 10 ** length <= n < 7 * 10 ** length:
            return acc(chart, "receivables"), f"Debitor {number}"
        return acc(chart, "payables"), f"Kreditor {number}"
    return number.zfill(length)[-4:], None


async def import_datev(conn: AsyncConnection, ledger: Any, tenant_id: UUID, content: bytes, filename: str | None, actor: str, *,
                       dry_run: bool) -> dict[str, Any]:
    from app.services.accounting import ROLES, Line, acc, chart_of, post

    chart = await chart_of(conn, tenant_id)
    meta, rows, errors = parse_datev(content)
    automatic = {ROLES["revenue_19"][0 if chart == "SKR03" else 1]: ("output", D("19")),
                 ROLES["revenue_7"][0 if chart == "SKR03" else 1]: ("output", D("7"))}
    prepared: list[dict[str, Any]] = []
    for r in rows:
        acc_a, note_a = _map_account(chart, r["account"], meta["account_length"])
        acc_b, note_b = _map_account(chart, r["contra"], meta["account_length"])
        debit_acc, credit_acc = (acc_a, acc_b) if r["debit_side"] else (acc_b, acc_a)
        gross = r["amount"]
        tax = BU_TAX.get(r["bu"][-1:]) if r["bu"] else None
        if tax is None:
            tax = automatic.get(credit_acc) or automatic.get(debit_acc)
        lines = [Line(debit_acc, debit=gross, note=note_a if r["debit_side"] else note_b),
                 Line(credit_acc, credit=gross, note=note_b if r["debit_side"] else note_a)]
        if tax:
            side, rate = tax
            vat = (gross * rate / (100 + rate)).quantize(D("0.01"))
            role = f"{'input' if side == 'input' else 'output'}_vat_{rate.normalize()}"
            vat_acc = acc(chart, role)
            if side == "input":  # Aufwand netto, Vorsteuer separat
                lines = [Line(debit_acc, debit=gross - vat), Line(vat_acc, debit=vat), lines[1]]
            else:
                lines = [lines[0], Line(credit_acc, credit=gross - vat), Line(vat_acc, credit=vat)]
        prepared.append({**r, "lines": lines, "debit_account": debit_acc, "credit_account": credit_acc, "vat": bool(tax)})
    numbers = {ln.account for p in prepared for ln in p["lines"]}
    known = {x[0] for x in await conn.execute(text("SELECT number FROM accounts WHERE chart = :c"), {"c": chart})}
    new_accounts = sorted(numbers - known)
    total = sum((p["amount"] for p in prepared), ZERO)
    summary = {"rows": len(prepared), "total": str(total), "new_accounts": new_accounts, "errors": len(errors),
               "period": f"{meta.get('period_from') or ''} – {meta.get('period_to') or ''}"}
    if not dry_run:
        if errors:
            raise ConflictError(f"Die Datei enthält {len(errors)} fehlerhafte Zeilen – bitte korrigieren und erneut hochladen")
        if not prepared:
            raise ConflictError("Keine Buchungen in der Datei")
        sha = _sha(content)
        await _record_import(conn, tenant_id, "datev", filename, sha, len(prepared), summary, actor)
        await ensure_accounts(conn, chart, numbers)
        for p in prepared:
            await post(conn, ledger, tenant_id, chart=chart, entry_date=p["date"], description=(p["text"] or "DATEV-Import")[:300],
                       document_ref=p["document"], source_type="import", source_id=uuid.uuid5(IMPORT_NS, f"datev:{sha}:{p['line']}"),
                       lines=p["lines"], method="manual", actor=actor)
    preview = [{"line": p["line"], "date": p["date"], "debit": p["debit_account"], "credit": p["credit_account"], "amount": p["amount"],
                "vat": p["vat"], "text": p["text"], "document": p["document"]} for p in prepared[:100]]
    return {"summary": summary, "meta": {k: str(v) if v else None for k, v in meta.items()}, "preview": preview, "errors": errors[:100]}
