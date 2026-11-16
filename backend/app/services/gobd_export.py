"""Datenträgerüberlassung für die Betriebsprüfung (Z3, § 147 Abs. 6 AO) im GDPdU-Beschreibungsstandard.

Das ZIP enthält je Tabelle eine CSV-Datei (Semikolon, Dezimalkomma, UTF-8) und eine ``index.xml`` nach
„Beschreibungsstandard für die Datenträgerüberlassung“ (DTD gdpdu-01-08-2002.dtd), die Prüfsoftware (z. B. IDEA)
direkt einlesen kann. Zusätzlich: Prüfsummen aller Dateien und das Ergebnis der Hash-Ketten-Prüfung.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import zipfile
from decimal import Decimal
from typing import Any
from uuid import UUID
from xml.sax.saxutils import escape

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# (Spaltenname, Typ: "A" alphanumerisch, "N2" numerisch 2 Nachkommastellen, "D" Datum)
TABLES: list[tuple[str, str, str, list[tuple[str, str]], str]] = [
    ("buchungsjournal.csv", "Buchungsjournal", "Alle Buchungssätze (Soll/Haben je Zeile), festgeschrieben im Ledger",
     [("Buchungsnummer", "A"), ("Buchungsdatum", "D"), ("Belegdatum", "D"), ("Belegnummer", "A"), ("Buchungstext", "A"),
      ("Konto", "A"), ("Soll", "N2"), ("Haben", "N2"), ("Kontenrahmen", "A"), ("Quelle", "A"), ("Verfahren", "A"),
      ("Erfasst_am", "A"), ("Erfasst_von", "A"), ("Ledger_Hash", "A")],
     """SELECT j.number, j.entry_date, COALESCE(j.document_date, j.entry_date), j.document_ref, j.description, l.account, l.debit, l.credit,
               j.chart, j.source_type, j.method, to_char(j.created_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI:SS'), j.created_by,
               j.ledger_hash
        FROM journal_entries j JOIN journal_lines l ON l.entry_id = j.id
        WHERE j.tenant_id = :t AND j.entry_date BETWEEN :s AND :e ORDER BY j.number, l.position"""),
    ("konten.csv", "Kontenplan", "Verwendete Sachkonten",
     [("Konto", "A"), ("Bezeichnung", "A"), ("Kontenart", "A"), ("Kontenrahmen", "A")],
     """SELECT DISTINCT a.number, a.name, a.type, a.chart FROM accounts a JOIN journal_entries j ON j.chart = a.chart
        JOIN journal_lines l ON l.entry_id = j.id AND l.account = a.number WHERE j.tenant_id = :t ORDER BY a.number"""),
    ("ausgangsrechnungen.csv", "Ausgangsrechnungen", "Ausgestellte Rechnungen und Stornorechnungen",
     [("Rechnungsnummer", "A"), ("Art", "A"), ("Rechnungsdatum", "D"), ("Faellig", "D"), ("Kundennummer", "A"), ("Kunde", "A"),
      ("Netto", "N2"), ("Umsatzsteuer", "N2"), ("Brutto", "N2"), ("Storniert_durch", "A"), ("Ledger_Hash", "A")],
     """SELECT i.number, i.kind, i.issue_date, i.due_date, c.number,
               COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || coalesce(c.last_name,''))),
               i.total_net, i.total_tax, i.total_gross, s.number, i.ledger_hash
        FROM invoices i JOIN customers c ON c.id = i.customer_id LEFT JOIN invoices s ON s.id = i.cancelled_by
        WHERE i.tenant_id = :t AND i.status = 'issued' AND i.issue_date BETWEEN :s AND :e ORDER BY i.number"""),
    ("eingangsbelege.csv", "Eingangsbelege", "Gebuchte Eingangsrechnungen/Belege inkl. Stornos",
     [("Belegdatum", "D"), ("Lieferant", "A"), ("Rechnungsnummer", "A"), ("Beschreibung", "A"), ("Steuerfall", "A"),
      ("Netto", "N2"), ("Steuer_auf_Beleg", "N2"), ("Brutto", "N2"), ("Vorsteuer", "N2"), ("Steuer_13b", "N2"),
      ("Art", "A"), ("Datei_SHA256", "A"), ("Ledger_Hash", "A")],
     """SELECT r.receipt_date, s.name, r.invoice_number, r.description, r.tax_case, r.net_amount, r.tax_amount, r.gross_amount,
               r.input_tax_amount, r.rc_tax_amount, r.kind, f.sha256, r.ledger_hash
        FROM receipts r JOIN suppliers s ON s.id = r.supplier_id LEFT JOIN receipt_files f ON f.id = r.file_id
        WHERE r.tenant_id = :t AND r.status = 'booked' AND r.receipt_date BETWEEN :s AND :e ORDER BY r.receipt_date"""),
    ("zahlungen.csv", "Zahlungseingaenge", "Zahlungen zu Ausgangsrechnungen (stornierte mit Datum)",
     [("Zahlungsdatum", "D"), ("Rechnungsnummer", "A"), ("Betrag", "N2"), ("Zahlart", "A"), ("Herkunft", "A"),
      ("Storniert_am", "A"), ("Erfasst_von", "A")],
     """SELECT p.paid_on, i.number, p.amount, p.method, p.source,
               to_char(p.reversed_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI'), p.created_by
        FROM payments p JOIN invoices i ON i.id = p.invoice_id
        WHERE p.tenant_id = :t AND p.paid_on BETWEEN :s AND :e ORDER BY p.paid_on"""),
    ("debitoren.csv", "Debitoren", "Kundenstammdaten",
     [("Kundennummer", "A"), ("Typ", "A"), ("Name", "A"), ("Strasse", "A"), ("PLZ", "A"), ("Ort", "A"), ("Land", "A"), ("USt_IdNr", "A")],
     """SELECT c.number, c.customer_type, COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || coalesce(c.last_name,''))),
               c.street, c.postal_code, c.city, c.country_code, c.vat_id FROM customers c WHERE c.tenant_id = :t ORDER BY c.number"""),
    ("kreditoren.csv", "Kreditoren", "Lieferantenstammdaten",
     [("Kreditor", "A"), ("Name", "A"), ("Land", "A"), ("USt_IdNr", "A")],
     """SELECT COALESCE(s.datev_account::text, ''), s.name, s.country_code, s.vat_id FROM suppliers s
        WHERE s.tenant_id = :t ORDER BY s.name"""),
]


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == "N2":
        return f"{Decimal(value):.2f}".replace(".", ",")
    if kind == "D" and isinstance(value, dt.date):
        return value.strftime("%d.%m.%Y")
    return str(value).replace("\r", " ").replace("\n", " ")


def _index_xml(company: str, start: dt.date, end: dt.date) -> str:
    tables = []
    for url, name, desc, cols, _sql in TABLES:
        columns = []
        for i, (col, kind) in enumerate(cols):
            tag = "VariablePrimaryKey" if i == 0 else "VariableColumn"
            if kind == "N2":
                body = "<Numeric><Accuracy>2</Accuracy></Numeric>"
            elif kind == "D":
                body = "<Date><Format>DD.MM.YYYY</Format></Date>"
            else:
                body = "<AlphaNumeric/>"
            columns.append(f"        <{tag}><Name>{escape(col)}</Name>{body}</{tag}>")
        tables.append(f"""    <Table>
      <URL>{url}</URL>
      <Name>{escape(name)}</Name>
      <Description>{escape(desc)}</Description>
      <Validity><Range><From>{start:%d.%m.%Y}</From><To>{end:%d.%m.%Y}</To></Range></Validity>
      <UTF8/>
      <DecimalSymbol>,</DecimalSymbol>
      <DigitGroupingSymbol>.</DigitGroupingSymbol>
      <VariableLength>
        <ColumnDelimiter>;</ColumnDelimiter>
        <RecordDelimiter>&#13;&#10;</RecordDelimiter>
        <TextEncapsulator>"</TextEncapsulator>
{chr(10).join(columns)}
      </VariableLength>
    </Table>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE DataSet SYSTEM "gdpdu-01-08-2002.dtd">
<DataSet>
  <Version>1.0</Version>
  <DataSupplier>
    <Name>{escape(company)}</Name>
    <Location>Deutschland</Location>
    <Comment>Datenträgerüberlassung nach § 147 Abs. 6 AO (Z3), erzeugt mit OpenEmployed</Comment>
  </DataSupplier>
  <Media>
    <Name>Datenträger 1</Name>
{chr(10).join(tables)}
  </Media>
</DataSet>
"""


async def export(conn: AsyncConnection, ledger: Any, tenant_id: UUID, start: dt.date, end: dt.date, actor: str) -> tuple[bytes, str]:
    from app.services import audit
    from app.services.masterdata import current_company_profile

    profile = await current_company_profile(conn, tenant_id, end)
    company = profile["legal_name"] if profile else "Unternehmen"
    files: dict[str, bytes] = {}
    for url, _name, _desc, cols, sql in TABLES:
        rows = (await conn.execute(text(sql), {"t": tenant_id, "s": start, "e": end})).all()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow([c for c, _ in cols])
        for row in rows:
            w.writerow([_fmt(v, kind) for v, (_c, kind) in zip(row, cols, strict=True)])
        files[url] = buf.getvalue().encode("utf-8")
    files["index.xml"] = _index_xml(company, start, end).encode("utf-8")
    verify = await ledger.verify() if ledger is not None else None
    lines = [f"GoBD-Datenexport (Z3) {company}", f"Zeitraum: {start:%d.%m.%Y} – {end:%d.%m.%Y}",
             f"Erstellt: {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC durch {actor}",
             f"Hash-Kette: {'intakt' if verify and verify.ok else 'NICHT INTAKT' if verify else 'nicht geprüft'}"
             + (f" ({verify.records_checked} Datensätze geprüft)" if verify else ""), "", "SHA-256 der Dateien:"]
    lines += [f"  {hashlib.sha256(data).hexdigest()}  {name}" for name, data in sorted(files.items())]
    files["pruefprotokoll.txt"] = "\r\n".join(lines).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    archive = out.getvalue()
    sha = hashlib.sha256(archive).hexdigest()
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="gobd.exported", entity_type="gobd_export", entity_id=None,
                       after={"from": start, "to": end, "sha256": sha})
    return archive, sha
