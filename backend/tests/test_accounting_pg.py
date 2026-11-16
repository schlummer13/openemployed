"""Doppelte Buchführung: Regelbuchungen, Claude-Kontierung (Attrappe), Summen- und Saldenliste."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import app
from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
TODAY = today_berlin()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x03" * 50


class FakeProvider:
    """KI-Anbieter-Attrappe (lokal oder Claude – gleiche Schnittstelle)."""

    kind = "ollama"
    model = "qwen2.5vl:7b"
    label = "Lokale KI (qwen2.5vl:7b)"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, *, system: str, text: str, file: tuple[bytes, str] | None, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"system": system, "text": text, "file": file, "schema": schema})
        return self.answer

    async def status(self) -> tuple[bool, str | None]:
        return True, None


def fake_extractor(answer: dict[str, Any]) -> FakeProvider:
    return FakeProvider(answer)


async def receipt(api: Any, supplier: str, net: str, tax: str, case: str = "IN_DE_19", category: str = "office", png: bytes = PNG) -> str:
    up = (await api.post("/api/receipt-files", files={"file": ("b.png", png, "image/png")})).json()
    r = await api.post("/api/receipts", json={"supplier_id": supplier, "file_id": up["id"], "receipt_date": TODAY.isoformat(),
                                              "invoice_number": f"R-{net}", "description": "Monitor und Kabel", "category": category,
                                              "tax_case": case, "net_amount": net, "tax_amount": tax})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_rule_postings_and_balances(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    await api.post(f"/api/invoices/{inv['id']}/payments", json={"paid_on": TODAY.isoformat(), "amount": "2092.02"})
    supplier = (await api.post("/api/suppliers", json={"name": "Bürohandel GmbH"})).json()["id"]
    rid = await receipt(api, supplier, "100.00", "19.00")
    await api.post(f"/api/receipts/{rid}/book")

    j = (await api.get("/api/accounting/journal")).json()
    assert j["created"] == 3 and j["chart"] == "SKR04"
    assert (await api.get("/api/accounting/journal")).json()["created"] == 0, "idempotent"
    by_source = {e["source_type"]: e for e in j["entries"]}
    inv_lines = {(ln["account"], ln["debit"], ln["credit"]) for ln in by_source["invoice"]["lines"]}
    assert inv_lines == {("1200", "2092.02", "0.00"), ("4400", "0.00", "1758.00"), ("3806", "0.00", "334.02")}
    rec = {(ln["account"], ln["debit"], ln["credit"]) for ln in by_source["receipt"]["lines"]}
    assert rec == {("6815", "100.00", "0.00"), ("1406", "19.00", "0.00"), ("3300", "0.00", "119.00")}
    assert all(e["method"] == "rule" for e in j["entries"])

    b = (await api.get("/api/accounting/balances")).json()
    assert b["balanced"] and Decimal(b["revenue"]) == Decimal("1758.00") and Decimal(b["expenses"]) == Decimal("100.00")
    assert Decimal(b["result"]) == Decimal("1658.00")
    bank = next(a for a in b["accounts"] if a["account"] == "1800")
    assert Decimal(bank["balance"]) == Decimal("2092.02")

    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE journal_lines SET debit = debit + 1"))
    async with env.engine.begin() as conn:
        entry = await conn.scalar(text("SELECT id FROM journal_entries LIMIT 1"))
        with pytest.raises(DBAPIError):  # unausgeglichen → Constraint-Trigger beim Commit
            async with conn.begin_nested():
                await conn.execute(text("INSERT INTO journal_lines (entry_id, position, account, debit) VALUES (:e, 99, '6300', 5)"),
                                   {"e": entry})
                await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_claude_booking(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    supplier = (await api.post("/api/suppliers", json={"name": "Elektronik AG"})).json()["id"]
    rid = await receipt(api, supplier, "1200.00", "228.00", category="hardware_gwg")

    app.state.ai = None
    assert (await api.post(f"/api/receipts/{rid}/ai-book")).status_code == 503

    app.state.ai = fake_extractor({"lines": [{"account": "9999", "amount": "1200.00", "note": "x"}], "category": "office",
                                          "booking_text": "Monitor", "reasoning": "r", "confidence": 0.9})
    bad = await api.post(f"/api/receipts/{rid}/ai-book")
    assert bad.status_code == 422 and "nicht zulässig" in bad.json()["detail"]
    assert (await api.get(f"/api/receipts/{rid}")).json()["status"] == "draft", "bei Fehler wird nichts gebucht"

    app.state.ai = fake_extractor({"lines": [{"account": "0650", "amount": "1000.00", "note": "Monitor"},
                                                    {"account": "6815", "amount": "150.00", "note": "Kabel"}],
                                          "category": "office", "booking_text": "Monitor", "reasoning": "r", "confidence": 0.9})
    off = await api.post(f"/api/receipts/{rid}/ai-book")
    assert off.status_code == 422 and "nicht exakt" in off.json()["detail"]

    ext = fake_extractor({"lines": [{"account": "0650", "amount": "1000.00", "note": "Monitor 32 Zoll (Anlagevermögen)"},
                                    {"account": "6815", "amount": "200.00", "note": "Kabel und Zubehör"}],
                          "category": "office", "booking_text": "Elektronik AG Monitor + Zubehör",
                          "reasoning": "Monitor über 800 € netto ist Anlagevermögen, Zubehör Bürobedarf.", "confidence": 0.92})
    app.state.ai = ext
    ok = await api.post(f"/api/receipts/{rid}/ai-book")
    assert ok.status_code == 200, ok.text
    sent = ext.calls[0]
    assert sent["file"] is not None and sent["file"][1] == "image/png", "Beleg wird der KI mitgegeben"
    assert "0650 Büroeinrichtung" in sent["text"] and "SKR04" in sent["system"]
    r = (await api.get(f"/api/receipts/{rid}")).json()
    assert r["status"] == "booked" and r["category"] == "office", "Kategorie aus der Kontierung übernommen"
    entry = next(e for e in (await api.get("/api/accounting/journal")).json()["entries"] if e["source_type"] == "receipt")
    assert entry["method"] == "ai" and entry["ai_model"].startswith("Lokale KI") and "Anlagevermögen" in entry["ai_reasoning"]
    assert entry["number"].startswith("BU-") and entry["ledger_hash"], "lückenlose Ledger-Nummer + Hash"
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in entry["lines"]} == {
        ("0650", "1000.00", "0.00"), ("6815", "200.00", "0.00"), ("1406", "228.00", "0.00"), ("3300", "0.00", "1428.00")}
    assert (await api.post(f"/api/receipts/{rid}/ai-book")).status_code == 422, "nicht doppelt buchen"

    # Reverse Charge: System bucht 13b-Vor- und Umsatzsteuer selbst
    rc_supplier = (await api.post("/api/suppliers", json={"name": "Anthropic, PBC", "country_code": "US"})).json()["id"]
    rc = await receipt(api, rc_supplier, "50.00", "0.00", case="IN_13B_DRITTLAND_SERVICE", category="software", png=PNG + b"rc")
    app.state.ai = fake_extractor({"lines": [{"account": "6837", "amount": "50.00", "note": "API"}], "category": "software",
                                          "booking_text": "Claude API", "reasoning": "Lizenz/Nutzungsrecht", "confidence": 0.95})
    assert (await api.post(f"/api/receipts/{rc}/ai-book")).status_code == 200
    entry = next(e for e in (await api.get("/api/accounting/journal")).json()["entries"] if e["source_id"] == rc)
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in entry["lines"]} == {
        ("6837", "50.00", "0.00"), ("1407", "9.50", "0.00"), ("3837", "0.00", "9.50"), ("3300", "0.00", "50.00")}


async def test_gobd_festschreibung(env: Env) -> None:  # noqa: F811
    """Periodensperre, Vollständigkeitsprüfung vor dem Abschluss, lückenlose Buchungsnummern."""
    import datetime as dt

    from app.services import accounting as ac

    api = env.api
    b2b, _ = await setup_basics(env)
    supplier = (await api.post("/api/suppliers", json={"name": "Bürohandel GmbH"})).json()["id"]
    prev = (TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=15)
    prev_month = f"{prev:%Y-%m}"
    up = (await api.post("/api/receipt-files", files={"file": ("b.png", PNG + b"g", "image/png")})).json()
    draft_r = (await api.post("/api/receipts", json={"supplier_id": supplier, "file_id": up["id"], "receipt_date": prev.isoformat(),
                                                     "invoice_number": "ALT-1", "description": "Papier", "category": "office",
                                                     "tax_case": "IN_DE_19", "net_amount": "10.00", "tax_amount": "1.90"})).json()

    # Abschluss mit offenem Belegentwurf → blockiert, erst nach ausdrücklicher Bestätigung möglich
    r = await api.post(f"/api/tax/vat/{prev_month}/lock")
    assert r.status_code == 409 and r.json()["detail"]["open_items"][0]["kind"] == "receipt_drafts"
    ok = await api.post(f"/api/tax/vat/{prev_month}/lock?confirm_open_items=true")
    assert ok.status_code == 200, ok.text
    sealed = [x for x in env.ledger.records if x.record_type == "vat_return.locked"][-1]
    assert sealed.payload["open_items_confirmed"], "Bestätigung wird mit festgeschrieben"

    # Beleg aus gesperrtem Monat: Buchung in die nächste offene Periode, Belegdatum bleibt erhalten
    assert (await api.post(f"/api/receipts/{draft_r['id']}/book")).status_code == 200
    j = (await api.get("/api/accounting/journal?start=" + prev.replace(day=1).isoformat())).json()
    entry = next(e for e in j["entries"] if e["document_ref"] == "ALT-1")
    assert entry["document_date"] == prev.isoformat() and entry["entry_date"] == TODAY.replace(day=1).isoformat()
    assert "Periode festgeschrieben" in entry["description"]

    # Ledger lehnt Festschreibung in gesperrte Periode ab (Verteidigung in der Tiefe)
    with pytest.raises(Exception, match="festgeschrieben"):
        await env.ledger.seal(tenant_id=entry_tenant(), record_type="journal.posted", payload={}, sealed_by="t", posting_period=prev_month)

    # Lückenlos: geht die DB-Transaktion nach der Festschreibung verloren, übernimmt die Wiederholung die Nummer
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    async with env.engine.connect() as conn:
        tx = await conn.begin()
        await ac.ensure_postings(conn, env.ledger, entry_tenant())
        await tx.rollback()
    numbers_before = sorted(e["number"] for e in (await api.get("/api/accounting/journal")).json()["entries"])
    all_sealed = sorted(x.document_number for x in env.ledger.records if x.record_type == "journal.posted")
    assert numbers_before == all_sealed, "jede festgeschriebene Nummer erscheint genau einmal im Journal"
    seqs = [int(n.split("-")[-1]) for n in all_sealed]
    assert seqs == list(range(1, len(seqs) + 1)), "keine Lücken"


def entry_tenant() -> Any:
    from app.core.config import get_settings

    return get_settings().tenant_id


async def test_gobd_export_and_integrity(env: Env) -> None:  # noqa: F811
    import csv
    import io
    import zipfile

    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    await api.post(f"/api/invoices/{inv['id']}/issue")
    r = await api.get("/api/gobd/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"index.xml", "buchungsjournal.csv", "ausgangsrechnungen.csv", "pruefprotokoll.txt"} <= names
    index = z.read("index.xml").decode()
    assert "gdpdu-01-08-2002.dtd" in index and "<URL>buchungsjournal.csv</URL>" in index
    rows = list(csv.reader(io.StringIO(z.read("buchungsjournal.csv").decode()), delimiter=";"))
    assert rows[0][:3] == ["Buchungsnummer", "Buchungsdatum", "Belegdatum"] and len(rows) == 4
    assert all(row[13] for row in rows[1:]), "jede Zeile mit Ledger-Hash"
    assert "Hash-Kette: intakt" in z.read("pruefprotokoll.txt").decode()

    status = (await api.get("/api/gobd/status")).json()
    assert status["chain_ok"] is True
    env.ledger.broken = True  # type: ignore[attr-defined]
    run = (await api.post("/api/automations/ledger_verify/run")).json()
    assert run["status"] == "error"
    tasks = (await api.get("/api/dashboard")).json()["tasks"]
    assert tasks[0]["severity"] == "red" and "Hash-Kette" in tasks[0]["title"]


class SequenceProvider(FakeProvider):
    """Liefert nacheinander verschiedene Antworten (erste mit Dokument, zweite ohne)."""

    def __init__(self, *answers: dict[str, Any]) -> None:
        super().__init__(answers[0])
        self.answers = list(answers)

    async def complete_json(self, *, system: str, text: str, file: tuple[bytes, str] | None, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"system": system, "text": text, "file": file, "schema": schema})
        return self.answers[min(len(self.calls), len(self.answers)) - 1]


async def test_ai_booking_retries_without_document_when_check_fails(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    supplier = (await api.post("/api/suppliers", json={"name": "Steuerkanzlei Weber"})).json()["id"]
    rid = await receipt(api, supplier, "450.00", "85.50", category="bookkeeping")
    seq = SequenceProvider(
        {"lines": [{"account": "0670", "amount": "450.00", "note": "GWG"}, {"account": "0675", "amount": "450.00", "note": "GWG"}],
         "category": "bookkeeping", "booking_text": "x", "reasoning": "x", "confidence": 0.5},
        {"lines": [{"account": "6830", "amount": "450.00", "note": "Buchführung"}], "category": "bookkeeping",
         "booking_text": "Finanzbuchhaltung", "reasoning": "Buchführungskosten", "confidence": 0.9})
    app.state.ai = seq
    ok = await api.post(f"/api/receipts/{rid}/ai-book")
    assert ok.status_code == 200, ok.text
    assert [c["file"] is not None for c in seq.calls] == [True, False], "zweiter Versuch ohne Dokumenttext"
    assert "Übliches Konto für diese Kategorie: 6830" in seq.calls[0]["text"]
    assert seq.calls[0]["schema"]["properties"]["lines"]["items"]["properties"]["account"]["enum"], "nur erlaubte Konten"
    entry = next(e for e in (await api.get("/api/accounting/journal")).json()["entries"] if e["source_type"] == "receipt")
    assert ("6830", "450.00", "0.00") in {(ln["account"], ln["debit"], ln["credit"]) for ln in entry["lines"]}


class TruncatingProvider(SequenceProvider):
    """Erste Antwort unbrauchbar (z. B. abgeschnittenes JSON), zweite korrekt."""

    async def complete_json(self, *, system: str, text: str, file: tuple[bytes, str] | None, schema: dict[str, Any]) -> dict[str, Any]:
        from app.services.ai import AIError

        self.calls.append({"system": system, "text": text, "file": file, "schema": schema})
        if len(self.calls) == 1:
            raise AIError("Antwort der lokalen KI ist kein gültiges JSON")
        return self.answers[-1]


async def test_ai_booking_retries_after_unusable_answer(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    supplier = (await api.post("/api/suppliers", json={"name": "Steuerkanzlei Weber"})).json()["id"]
    rid = await receipt(api, supplier, "450.00", "85.50", category="bookkeeping")
    good = {"lines": [{"account": "6830", "amount": "450.00", "note": "Buchführung"}], "category": "bookkeeping",
            "booking_text": "Finanzbuchhaltung", "reasoning": "Buchführungskosten", "confidence": 0.9}
    app.state.ai = prov = TruncatingProvider(good)
    ok = await api.post(f"/api/receipts/{rid}/ai-book")
    assert ok.status_code == 200, ok.text
    assert len(prov.calls) == 2 and prov.calls[1]["file"] is None
    assert prov.calls[0]["schema"]["properties"]["reasoning"]["maxLength"] == 300
