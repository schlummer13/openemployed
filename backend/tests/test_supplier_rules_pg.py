"""Lieferanten-Regeln: einmal einsortieren, danach automatisch kontieren; Buchung nur per Klick (auch als Sammelbuchung)."""

from __future__ import annotations

from typing import Any

import pytest

from app.main import app
from app.services.mail import Attachment, IncomingMail
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import PNG, receipt
from tests.test_automation_pg import InboxMailer
from tests.test_extraction import BASE
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


class RoutingExtractor:
    """Erkennung je Datei: der Marker im Dateiinhalt bestimmt Lieferant und Rechnung."""

    def __init__(self, by_marker: dict[bytes, dict[str, Any]]) -> None:
        self.by_marker = by_marker

    async def extract(self, content: bytes, mime_type: str, context: str) -> tuple[dict[str, Any], dict[str, int], str]:
        data = next(v for k, v in self.by_marker.items() if k in content)
        return BASE | data, {"input": 1, "output": 1}, "test"


def png(marker: bytes) -> bytes:
    return PNG + marker


async def test_supplier_rule_sets_account_and_journal(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    accounts = {a["number"] for a in (await api.get("/api/receipt-accounts")).json()}
    assert "6310" in accounts and "0690" in accounts and "1800" not in accounts and "1406" not in accounts

    bad = await api.post("/api/suppliers", json={"name": "Bank", "default_account": "1800"})
    assert bad.status_code == 422 and "nicht zulässig" in bad.text
    regus = (await api.post("/api/suppliers", json={"name": "Regus Düsseldorf", "default_category": "rent",
                                                      "default_account": "6310"})).json()
    assert regus["default_account"] == "6310" and regus["needs_review"] is False

    rid = await receipt(api, regus["id"], "690.00", "131.10", category="rent")
    r = (await api.get(f"/api/receipts/{rid}")).json()
    assert r["expense_account"] == "6310", "Konto aus der Lieferanten-Regel"
    assert r["ready"] is True and r["blockers"] == [] and r["hints"] == []
    assert (await api.post(f"/api/receipts/{rid}/book")).status_code == 200
    entry = next(e for e in (await api.get("/api/accounting/journal")).json()["entries"] if e["source_type"] == "receipt")
    assert ("6310", "690.00", "0.00") in {(ln["account"], ln["debit"], ln["credit"]) for ln in entry["lines"]}

    # abweichendes Konto für einen einzelnen Beleg; unzulässiges Konto wird abgewiesen
    rid2 = await receipt(api, regus["id"], "120.00", "22.80", category="rent", png=png(b"nk"))
    upd = await api.put(f"/api/receipts/{rid2}", json={**_body(await api.get(f"/api/receipts/{rid2}")), "expense_account": "1800"})
    assert upd.status_code == 422


def _body(resp: Any) -> dict[str, Any]:
    r = resp.json()
    return {k: r[k] for k in ("supplier_id", "file_id", "receipt_date", "invoice_number", "description", "category", "tax_case",
                              "net_amount", "tax_amount")}


async def test_inbox_new_supplier_once_then_automatic(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    hetzner = {"supplier_name": "Hetzner Online GmbH", "supplier_vat_id": "DE812871812", "category": "software",
               "description": "Server", "net_amount": "89.00", "tax_amount": "16.91", "gross_amount": "105.91"}
    app.state.extractor = RoutingExtractor({
        b"H1": hetzner | {"invoice_number": "H-1"},
        b"H2": hetzner | {"invoice_number": "H-2"},
        b"H3": hetzner | {"invoice_number": "H-3"},
    })
    app.state.mailer = mailer = InboxMailer([
        IncomingMail("1", "<a@h>", "rechnung@hetzner.de", "Rechnung", None, "", (Attachment("h1.png", "image/png", png(b"H1")),)),
        IncomingMail("2", "<b@h>", "rechnung@hetzner.de", "Rechnung", None, "", (Attachment("h2.png", "image/png", png(b"H2")),)),
    ])
    assert (await api.post("/api/automations/receipt_inbox/run")).json()["status"] == "ok"
    sup = next(s for s in (await api.get("/api/suppliers")).json() if s["name"] == "Hetzner Online GmbH")
    assert sup["needs_review"] is True, "neuer Lieferant muss erst einsortiert werden"
    drafts = (await api.get("/api/receipts")).json()
    assert len(drafts) == 2 and all(not d["ready"] and "einsortieren" in d["blockers"][0] for d in drafts)
    skipped = (await api.post("/api/receipts/book-ready", json={"receipt_ids": [d["id"] for d in drafts]})).json()
    assert skipped["booked"] == [] and len(skipped["skipped"]) == 2, "nicht einsortierte Belege werden nie gebucht"

    # einmal einsortieren: Konto 6495 (Wartung Hard-/Software), Kategorie Hosting – als Regel merken
    first = next(d for d in drafts if d["invoice_number"] == "H-1")
    saved = await api.put(f"/api/receipts/{first['id']}", json={
        **_body(await api.get(f"/api/receipts/{first['id']}")), "category": "hosting", "expense_account": "6495", "remember_rule": True})
    assert saved.status_code == 200, saved.text
    sup = next(s for s in (await api.get("/api/suppliers")).json() if s["name"] == "Hetzner Online GmbH")
    assert sup["needs_review"] is False and sup["default_account"] == "6495" and sup["default_category"] == "hosting"
    drafts = {d["invoice_number"]: d for d in (await api.get("/api/receipts")).json()}
    assert drafts["H-2"]["expense_account"] == "6495" and drafts["H-2"]["category"] == "hosting", "offener Beleg übernimmt die Regel"
    assert drafts["H-1"]["ready"] and drafts["H-2"]["ready"]

    # nächste Rechnung kommt automatisch kontiert an
    mailer.mails.append(IncomingMail("3", "<c@h>", "rechnung@hetzner.de", "Rechnung", None, "",
                                     (Attachment("h3.png", "image/png", png(b"H3")),)))
    await api.post("/api/automations/receipt_inbox/run")
    drafts = {d["invoice_number"]: d for d in (await api.get("/api/receipts")).json()}
    assert drafts["H-3"]["expense_account"] == "6495" and drafts["H-3"]["category"] == "hosting" and drafts["H-3"]["ready"]

    # Sammelbuchung per Klick
    res = (await api.post("/api/receipts/book-ready", json={"receipt_ids": [d["id"] for d in drafts.values()]})).json()
    assert len(res["booked"]) == 3 and res["skipped"] == []
    assert all(r["status"] == "booked" for r in (await api.get("/api/receipts")).json())


async def test_review_hints_block_batch_booking(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    amazon = (await api.post("/api/suppliers", json={"name": "Amazon EU", "default_category": "office",
                                                       "default_account": "6815"})).json()
    small = [await receipt(api, amazon["id"], net, "0.00", case="IN_OHNE_VST", category="office", png=png(net.encode()))
             for net in ("40.00", "42.00")]
    for rid in small:
        await api.post(f"/api/receipts/{rid}/book")
    big = await receipt(api, amazon["id"], "1299.00", "0.00", case="IN_OHNE_VST", category="office", png=png(b"big"))
    r = (await api.get(f"/api/receipts/{big}")).json()
    assert not r["ready"] and any("Anlagevermögen" in h for h in r["hints"]), "über 800 € netto → Prüfhinweis"
    assert any("weicht deutlich" in h for h in r["hints"]), "Betrag weit über dem Üblichen dieses Lieferanten"
    res = (await api.post("/api/receipts/book-ready", json={"receipt_ids": [big]})).json()
    assert res["booked"] == [] and "Anlagevermögen" in res["skipped"][0]["reason"]
    # bewusst einzeln buchen bleibt möglich (nach Prüfung), z. B. als Anlagevermögen
    fixed = await api.put(f"/api/receipts/{big}", json={**_body(await api.get(f"/api/receipts/{big}")), "category": "fixed_asset",
                                                          "expense_account": "0690"})
    assert fixed.status_code == 200 and not any("Anlagevermögen" in h for h in fixed.json()["warnings"])
    assert (await api.post(f"/api/receipts/{big}/book")).status_code == 200


async def test_extraction_uses_rule_only_when_account_is_set(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    app.state.extractor = RoutingExtractor({
        b"G1": {"supplier_name": "Google Ireland Ltd.", "supplier_vat_id": "IE6388047V", "supplier_country": "IE",
                "category": "software", "invoice_number": "G-1", "tax_amount": "0.00", "gross_amount": "100.00",
                "reverse_charge_note": True, "tax_rates_percent": []},
    })
    up = (await api.post("/api/receipt-files", files={"file": ("g.png", png(b"G1"), "image/png")})).json()
    plain = (await api.post("/api/suppliers", json={"name": "Google Ireland Ltd.", "country_code": "IE",
                                                    "vat_id": "IE6388047V"})).json()
    sug = (await api.post(f"/api/receipt-files/{up['id']}/extract")).json()
    assert sug["supplier_id"] == plain["id"] and sug["supplier_known"] is False
    assert sug["tax_case"].startswith("IN_13B"), "Standardwerte überschreiben erkanntes Reverse Charge nicht"
    await api.put(f"/api/suppliers/{plain['id']}", json={"name": "Google Ireland Ltd.", "country_code": "IE", "vat_id": "IE6388047V",
                                                         "default_tax_case": "IN_13B_EU_SERVICE", "default_category": "advertising",
                                                         "default_account": "6600"})
    sug = (await api.post(f"/api/receipt-files/{up['id']}/extract")).json()
    assert sug["supplier_known"] is True and sug["expense_account"] == "6600" and sug["category"] == "advertising"
    assert sug["tax_case"] == "IN_13B_EU_SERVICE" and sug["tax_amount"] == "0.00"


async def test_preview_and_effective_account(env: Env) -> None:  # noqa: F811
    from app.demo import pdf

    api = env.api
    await setup_basics(env)
    up = (await api.post("/api/receipt-files", files={"file": ("r.pdf", pdf("Rechnung 4711 - 119,00 EUR"), "application/pdf")})).json()
    r = await api.get(f"/api/receipt-files/{up['id']}/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-page-count"] == "1"
    assert r.content.startswith(b"\x89PNG")
    assert (await api.get(f"/api/receipt-files/{up['id']}/preview?page=2")).status_code == 404
    sup = (await api.post("/api/suppliers", json={"name": "Telekom", "default_category": "telecom"})).json()
    rid = await receipt(api, sup["id"], "49.95", "9.49", category="telecom", png=png(b"tk"))
    got = (await api.get(f"/api/receipts/{rid}")).json()
    assert got["expense_account"] is None and got["account"] == "6805" and got["account_name"] == "Telefon", "Konto aus der Kategorie"
    listed = next(x for x in (await api.get("/api/receipts")).json() if x["id"] == rid)
    assert listed["account"] == "6805"


async def test_mobile_capture_uses_supplier_rule(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    await api.post("/api/suppliers", json={"name": "Hetzner Online GmbH", "vat_id": "DE812871812", "default_category": "hosting",
                                           "default_account": "6495"})
    app.state.extractor = RoutingExtractor({b"M1": {"supplier_name": "Hetzner Online GmbH", "supplier_vat_id": "DE812871812",
                                                    "category": "software", "invoice_number": "M-1", "description": "Server"}})
    up = (await api.post("/api/receipt-files", files={"file": ("foto.png", png(b"M1"), "image/png")})).json()
    r = (await api.post(f"/api/receipt-files/{up['id']}/capture")).json()
    assert r["draft"] is True and r["receipt"]["expense_account"] == "6495" and r["receipt"]["category"] == "hosting"
    assert r["receipt"]["ready"] is True and r["receipt"]["source"] == "mobile"
    again = (await api.post(f"/api/receipt-files/{up['id']}/capture")).json()
    assert again["draft"] is False and "bereits" in again["notes"][0]


async def test_supplier_found_in_pdf_text_when_ai_misses_it(env: Env) -> None:  # noqa: F811
    from app.demo import pdf

    api = env.api
    await setup_basics(env)
    await api.post("/api/suppliers", json={"name": "Hetzner Online GmbH", "default_category": "hosting", "default_account": "6495"})
    app.state.extractor = RoutingExtractor({b"": {"supplier_name": None, "supplier_vat_id": None, "invoice_number": "H-7", "category": "software"}})
    doc = pdf("Hetzner Online GmbH - Rechnung H-7 - Server - Netto 100,00 EUR")
    up = (await api.post("/api/receipt-files", files={"file": ("h.pdf", doc, "application/pdf")})).json()
    r = (await api.post(f"/api/receipt-files/{up['id']}/capture")).json()
    assert r["draft"] is True and r["receipt"]["supplier_name"] == "Hetzner Online GmbH" and r["receipt"]["expense_account"] == "6495"
