"""E-Rechnung: CII-XML (XSD + EN-16931-Schematron), PDF/A-3 mit eingebettetem XML, GiroCode, Logo-Prüfung."""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal
from typing import Any

import pytest
from lxml import etree

from app.domain.invoice_calc import LineInput, PriceMode, calculate_invoice
from app.einvoice.cii import NS, build_cii
from app.einvoice.pdf import girocode_data_uri, render_html
from app.einvoice.validate import EInvoiceValidationError, check_schematron, validate_einvoice
from app.services.documents import LogoError, detect_logo_type
from app.services.invoices import build_snapshot

D = Decimal

SELLER = {
    "legal_name": "Beispiel Software UG (haftungsbeschränkt)", "legal_form": "UG (haftungsbeschränkt)",
    "street": "Königsallee 1", "postal_code": "40212", "city": "Düsseldorf", "country_code": "DE",
    "register_court": "Amtsgericht Düsseldorf", "register_number": "HRB 12345", "managing_directors": ["Anna", "Ben"],
    "tax_number": "103/5734/0815", "vat_id": None, "iban": "DE89370400440532013000", "bic": None,
    "bank_name": "Testbank", "email": "rechnung@beispiel.de", "phone": None, "website": None,
}


def buyer(kind: str = "b2b") -> dict[str, Any]:
    return {
        "customer_id": "x", "number": "K-10001", "type": kind, "name": "HR 1 GmbH" if kind == "b2b" else "Max Muster",
        "company_name": "HR 1 GmbH" if kind == "b2b" else None, "first_name": None, "last_name": None,
        "street": "Weg 1", "postal_code": "50667", "city": "Köln", "country_code": "DE", "email": "kunde@example.de",
        "vat_id": "DE123456789" if kind == "b2b" else None, "buyer_reference": "PO-4711" if kind == "b2b" else None,
    }


def snapshot(kind: str = "invoice", mode: PriceMode = PriceMode.NET, sign: int = 1, b: str = "b2b",
             period: tuple[str | None, str | None] = ("2026-11-01", "2026-11-30")) -> dict[str, Any]:
    lines = [
        LineInput("HR-Software <Basic> & mehr", D(sign), "MON", D("1758.00" if mode is PriceMode.NET else "2092.02"),
                  "OUT_DE_19"),
        LineInput("Sonderanfertigung", D("7.25") * sign, "HUR", D("95.00"), "OUT_DE_19"),
        LineInput("Fachbuch", D(3 * sign), "C62", D("12.99"), "OUT_DE_7"),
    ]
    totals = calculate_invoice(lines, mode)
    row = {"service_date_from": dt.date.fromisoformat(period[0]) if period[0] else None,
           "service_date_to": dt.date.fromisoformat(period[1]) if period[1] else None,
           "intro_text": "Vielen Dank für Ihren Auftrag.", "closing_text": None,
           "cancellation_reason": "Doppelt" if kind == "cancellation" else None}
    cancels = ({"invoice_id": "x", "number": "RE-2026-00001", "issue_date": "2026-11-14", "ledger_hash": "0" * 64}
               if kind == "cancellation" else None)
    return build_snapshot(kind=kind, issue_date=dt.date(2026, 11, 14), due_date=dt.date(2026, 11, 28), row=row,
                          totals=totals, seller=SELLER, buyer=buyer(b), cancels=cancels)


def _x(xml: bytes, path: str) -> list[str]:
    return [str(v) for v in etree.fromstring(xml).xpath(path, namespaces=NS)]


@pytest.mark.parametrize(
    ("kind", "mode", "sign", "b", "period"),
    [
        ("invoice", PriceMode.NET, 1, "b2b", ("2026-11-01", "2026-11-30")),
        ("invoice", PriceMode.GROSS, 1, "b2c", ("2026-11-01", "2026-11-30")),
        ("invoice", PriceMode.NET, 1, "b2b", ("2026-11-14", None)),
        ("cancellation", PriceMode.NET, -1, "b2b", ("2026-11-01", "2026-11-30")),
        ("cancellation", PriceMode.GROSS, -1, "b2c", ("2026-11-01", "2026-11-30")),
    ],
)
def test_xml_passes_xsd_and_all_en16931_rules(kind: str, mode: PriceMode, sign: int, b: str,
                                              period: tuple[str, str | None]) -> None:
    xml = build_cii("RE-2026-00001", snapshot(kind, mode, sign, b, period))
    warnings = validate_einvoice(xml)
    assert warnings == [], [(w.rule_id, w.text) for w in warnings]


def test_xml_content_invoice() -> None:
    s = snapshot()
    xml = build_cii("RE-2026-00001", s)
    assert _x(xml, "//rsm:ExchangedDocument/ram:TypeCode/text()") == ["380"]
    assert _x(xml, "//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID/text()") == ["urn:cen.eu:en16931:2017"]
    assert _x(xml, "//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount/text()") == [s["totals"]["gross"]]
    assert _x(xml, "//ram:BuyerReference/text()") == ["PO-4711"]
    assert _x(xml, "//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='FC']/text()") == ["103/5734/0815"]
    assert _x(xml, "//ram:PayeePartyCreditorFinancialAccount/ram:IBANID/text()") == ["DE89370400440532013000"]
    assert _x(xml, "//ram:DueDateDateTime/udt:DateTimeString/text()") == ["20261128"]
    assert sorted(_x(xml, "//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent/text()")) == ["19.00", "7.00"]
    # Sonderzeichen sicher kodiert
    assert _x(xml, "//ram:SpecifiedTradeProduct/ram:Name/text()")[0] == "HR-Software <Basic> & mehr"


def test_xml_content_cancellation_is_credit_note_with_positive_amounts() -> None:
    s = snapshot("cancellation", sign=-1)
    xml = build_cii("RE-2026-00002", s)
    assert _x(xml, "//rsm:ExchangedDocument/ram:TypeCode/text()") == ["381"]
    assert Decimal(_x(xml, "//ram:GrandTotalAmount/text()")[0]) == -Decimal(s["totals"]["gross"]) > 0
    assert all(Decimal(q) > 0 for q in _x(xml, "//ram:BilledQuantity/text()"))
    assert _x(xml, "//ram:InvoiceReferencedDocument/ram:IssuerAssignedID/text()") == ["RE-2026-00001"]
    assert _x(xml, "//ram:SpecifiedTradeSettlementPaymentMeans") == []


def test_schematron_detects_manipulation() -> None:
    xml = build_cii("RE-2026-00001", snapshot())
    broken = xml.replace(b"<ram:GrandTotalAmount>", b"<ram:GrandTotalAmount>9", 1)
    rules = {v.rule_id for v in check_schematron(broken)}
    assert "BR-CO-15" in rules
    with pytest.raises(EInvoiceValidationError):
        validate_einvoice(broken)


def test_html_escapes_user_text() -> None:
    s = snapshot()
    s["intro_text"] = "<script>alert(1)</script>"
    html = render_html("RE-2026-00001", s, logo_data_uri=None)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "HR-Software &lt;Basic&gt; &amp; mehr" in html


def test_html_contains_mandatory_information() -> None:
    html = render_html("RE-2026-00001", snapshot(), logo_data_uri=None)
    for needle in ("RE-2026-00001", "14.11.2026", "01.11.2026", "30.11.2026", "Amtsgericht Düsseldorf", "HRB 12345",
                   "103/5734/0815", "Anna, Ben", "DE89 3704 0044 0532 0130 00", "28.11.2026", "K-10001", "PO-4711"):
        assert needle in html, needle


def test_girocode_only_for_positive_amounts() -> None:
    uri = girocode_data_uri(snapshot(), "RE-2026-00001")
    assert uri is not None and uri.startswith("data:image/svg+xml")
    assert girocode_data_uri(snapshot("cancellation", sign=-1), "RE-2026-00002") is None


def test_logo_detection() -> None:
    assert detect_logo_type(b"\x89PNG\r\n\x1a\n" + b"0" * 10) == "image/png"
    assert detect_logo_type(b"\xff\xd8\xff\xe0" + b"0" * 10) == "image/jpeg"
    assert detect_logo_type(b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>') == "image/svg+xml"
    for bad in (b"", b"GIF89a....", b"<svg><script>alert(1)</script></svg>", b'<svg onload="x()"></svg>',
                b"<svg><foreignObject/></svg>", b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024)):
        with pytest.raises(LogoError):
            detect_logo_type(bad)


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


@pytest.mark.skipif(not _weasyprint_available(), reason="WeasyPrint/Pango nicht installiert (läuft im Test-Container)")
def test_pdf_is_pdfa3_with_embedded_facturx_xml() -> None:
    import facturx
    from pypdf import PdfReader

    from app.einvoice.pdf import render_facturx_pdf

    s = snapshot(b="b2c", mode=PriceMode.GROSS)
    xml = build_cii("RE-2026-00003", s)
    logo = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI4MCIgaGVpZ2h0PSIyMCI+PHJlY3Qgd2lkdGg9IjgwIiBoZWlnaHQ9IjIwIiBmaWxsPSIjMmM1NWRiIi8+PC9zdmc+"
    pdf = render_facturx_pdf("RE-2026-00003", s, xml, logo=logo)
    assert pdf.startswith(b"%PDF-")
    filename, embedded = facturx.get_xml_from_pdf(pdf, check_xsd=True)
    assert filename == "factur-x.xml"
    assert embedded == xml
    reader = PdfReader(io.BytesIO(pdf))
    xmp = reader.xmp_metadata
    assert xmp is not None
    raw = reader.trailer["/Root"]["/Metadata"].get_object().get_data()
    assert b"<pdfaid:part>3</pdfaid:part>" in raw or b'pdfaid:part="3"' in raw
    assert b"EN 16931" in raw
    text = "".join(page.extract_text() for page in reader.pages)
    assert "RE-2026-00003" in text


@pytest.mark.skipif(not _weasyprint_available(), reason="WeasyPrint/Pango nicht installiert (läuft im Test-Container)")
def test_pdf_blocks_external_resources() -> None:
    from app.einvoice.pdf import render_facturx_pdf

    s = snapshot()
    xml = build_cii("RE-2026-00001", s)
    # Externe URL als „Logo“ darf nicht geladen werden – Rendering läuft trotzdem durch (Bild fehlt)
    pdf = render_facturx_pdf("RE-2026-00001", s, xml, logo="http://169.254.169.254/latest/meta-data")
    assert pdf.startswith(b"%PDF-")
