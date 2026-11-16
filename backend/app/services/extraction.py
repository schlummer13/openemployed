"""Belegerkennung: E-Rechnung (XRechnung/ZUGFeRD, deterministisch) oder KI (Claude, strukturierte Ausgabe).

Grundsätze:
- Die KI liefert nur **Rohdaten** (Lieferant, Datum, Beträge, Hinweise). Den Steuerfall leitet der Code nach festen
  Regeln ab – steuerliche Entscheidungen trifft nicht das Modell.
- Ergebnisse sind **Vorschläge**: es wird nie automatisch gebucht. Rechenprüfungen erzeugen Warnungen.
- Jede Datei wird höchstens einmal an die KI übermittelt (Cache je Datei); Übermittlung nur auf ausdrückliche Anforderung.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from lxml import etree
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.expenses import CATEGORIES

log = logging.getLogger("openemployed.extraction")
D = Decimal
CENT = D("0.01")
EU = frozenset({"AT", "BE", "BG", "CY", "CZ", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV",
                "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"})


class ExtractionError(RuntimeError):
    pass


class ExtractionNotConfigured(RuntimeError):
    pass


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return D(str(v).replace(" ", "")).quantize(CENT)
    except InvalidOperation:
        return None


# ───────────────────────── E-Rechnung ─────────────────────────

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "ublcn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


def _safe_parse(xml: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False, load_dtd=False, huge_tree=False)
    try:
        return etree.fromstring(xml, parser)
    except etree.XMLSyntaxError as exc:
        raise ExtractionError(f"XML nicht lesbar: {exc}") from exc


def _t(node: etree._Element, path: str) -> str | None:
    found = node.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def _date102(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    if len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return v[:10] if len(v) >= 10 else None


def parse_einvoice(xml: bytes) -> dict[str, Any]:
    """Liest CII (ZUGFeRD/Factur-X/XRechnung-CII) oder UBL (XRechnung-UBL) in ein neutrales Rohformat."""
    root = _safe_parse(xml)
    tag = etree.QName(root).namespace
    if tag == NS["rsm"]:
        agr = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement"
        stl = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
        seller = root.find(f"{agr}/ram:SellerTradeParty", NS)
        vat = None
        if seller is not None:
            for reg in seller.findall("ram:SpecifiedTaxRegistration/ram:ID", NS):
                if reg.get("schemeID") == "VA" and reg.text:
                    vat = reg.text.strip()
        groups = []
        for tax in root.findall(f"{stl}/ram:ApplicableTradeTax", NS):
            groups.append({"category": _t(tax, "ram:CategoryCode"), "rate": _dec(_t(tax, "ram:RateApplicablePercent")),
                           "net": _dec(_t(tax, "ram:BasisAmount")), "tax": _dec(_t(tax, "ram:CalculatedAmount"))})
        summ = f"{stl}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"
        return {
            "format": "CII", "invoice_number": _t(root, "rsm:ExchangedDocument/ram:ID"),
            "type_code": _t(root, "rsm:ExchangedDocument/ram:TypeCode"),
            "receipt_date": _date102(_t(root, "rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString")),
            "service_date": _date102(_t(root, "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeDelivery/"
                                             "ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString")),
            "supplier_name": _t(seller, "ram:Name") if seller is not None else None, "supplier_vat_id": vat,
            "supplier_country": _t(seller, "ram:PostalTradeAddress/ram:CountryID") if seller is not None else None,
            "currency": _t(root, f"{stl}/ram:InvoiceCurrencyCode") or "EUR",
            "net_amount": _dec(_t(root, f"{summ}/ram:TaxBasisTotalAmount")),
            "tax_amount": _dec(_t(root, f"{summ}/ram:TaxTotalAmount")), "gross_amount": _dec(_t(root, f"{summ}/ram:GrandTotalAmount")),
            "groups": groups,
            "description": _t(root, "rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem/"
                                    "ram:SpecifiedTradeProduct/ram:Name"),
        }
    if tag in (NS["ubl"], NS["ublcn"]):
        party = root.find("cac:AccountingSupplierParty/cac:Party", NS)
        name = vat = country = None
        if party is not None:
            name = _t(party, "cac:PartyLegalEntity/cbc:RegistrationName") or _t(party, "cac:PartyName/cbc:Name")
            vat = _t(party, "cac:PartyTaxScheme/cbc:CompanyID")
            country = _t(party, "cac:PostalAddress/cac:Country/cbc:IdentificationCode")
        groups = []
        for sub in root.findall("cac:TaxTotal/cac:TaxSubtotal", NS):
            groups.append({"category": _t(sub, "cac:TaxCategory/cbc:ID"), "rate": _dec(_t(sub, "cac:TaxCategory/cbc:Percent")),
                           "net": _dec(_t(sub, "cbc:TaxableAmount")), "tax": _dec(_t(sub, "cbc:TaxAmount"))})
        return {
            "format": "UBL", "invoice_number": _t(root, "cbc:ID"),
            "type_code": "381" if tag == NS["ublcn"] else _t(root, "cbc:InvoiceTypeCode"),
            "receipt_date": _t(root, "cbc:IssueDate"), "service_date": _t(root, "cac:Delivery/cbc:ActualDeliveryDate"),
            "supplier_name": name, "supplier_vat_id": vat, "supplier_country": country,
            "currency": _t(root, "cbc:DocumentCurrencyCode") or "EUR",
            "net_amount": _dec(_t(root, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount")),
            "tax_amount": _dec(_t(root, "cac:TaxTotal/cbc:TaxAmount")),
            "gross_amount": _dec(_t(root, "cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount")),
            "groups": groups, "description": _t(root, "cac:InvoiceLine/cac:Item/cbc:Name"),
        }
    raise ExtractionError("Unbekanntes XML-Format (weder CII noch UBL)")


def embedded_xml(pdf: bytes) -> bytes | None:
    try:
        import facturx

        _name, xml = facturx.get_xml_from_pdf(pdf, check_xsd=False)
        return xml if xml else None
    except Exception:
        return None


# ───────────────────────── KI (Claude) ─────────────────────────

CATEGORY_CODES = [c.code for c in CATEGORIES]
AMOUNT = {"type": "string", "description": "Betrag mit Punkt als Dezimaltrenner, 2 Nachkommastellen, z. B. 1234.50"}
AI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_invoice": {"type": "boolean", "description": "false, wenn das Dokument keine Rechnung/Quittung ist"},
        "supplier_name": {"type": "string"},
        "supplier_vat_id": {"type": ["string", "null"], "description": "USt-IdNr des Rechnungsstellers, z. B. DE123456789 oder IE6388047V"},
        "supplier_country": {"type": "string", "description": "ISO-3166-1-alpha-2-Land des Rechnungsstellers"},
        "invoice_number": {"type": ["string", "null"]},
        "receipt_date": {"type": "string", "description": "Rechnungsdatum YYYY-MM-DD"},
        "service_date": {"type": ["string", "null"], "description": "Leistungsdatum/-ende YYYY-MM-DD, falls angegeben"},
        "currency": {"type": "string", "description": "ISO-4217, z. B. EUR"},
        "net_amount": AMOUNT,
        "tax_amount": AMOUNT,
        "gross_amount": AMOUNT,
        "tax_rates_percent": {"type": "array", "items": {"type": "string"}, "description": "alle Steuersätze, z. B. [\"19\"]"},
        "reverse_charge_note": {"type": "boolean", "description": "Hinweis auf Reverse Charge (Steuerschuldnerschaft)"},
        "small_business_note": {"type": "boolean", "description": "Hinweis auf Kleinunternehmerregelung (§ 19 UStG)"},
        "already_paid": {"type": "boolean"},
        "category": {"type": "string", "enum": CATEGORY_CODES},
        "description": {"type": "string", "description": "kurze deutsche Beschreibung der Leistung, max. 80 Zeichen"},
        "confidence": {"type": "number", "description": "Gesamtsicherheit 0 bis 1"},
        "notes": {"type": "string", "description": "Auffälligkeiten (unleserlich, mehrere Rechnungen, Fremdwährung …)"},
    },
    "required": ["is_invoice", "supplier_name", "supplier_vat_id", "supplier_country", "invoice_number", "receipt_date",
                 "service_date", "currency", "net_amount", "tax_amount", "gross_amount", "tax_rates_percent",
                 "reverse_charge_note", "small_business_note", "already_paid", "category", "description", "confidence", "notes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "Du liest Eingangsrechnungen und Quittungen einer deutschen UG und gibst die Daten exakt so wieder, wie sie auf dem "
    "Dokument stehen. Rechne nichts um und erfinde nichts: Fehlt ein Wert, gib null bzw. eine leere Zeichenkette an und "
    "vermerke es in notes. Beträge immer als Zahl mit Punkt und zwei Nachkommastellen. Bei Gutschriften negative Beträge. "
    "Ordne eine der vorgegebenen Kategorien zu. Der Rechnungsempfänger ist der Kunde, nicht der Lieferant."
)


class AIExtractor(Protocol):
    async def extract(self, content: bytes, mime_type: str, context: str) -> tuple[dict[str, Any], dict[str, int], str]: ...


class ProviderExtractor:
    """Belegerkennung über den konfigurierten KI-Anbieter (lokal oder Claude)."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    async def extract(self, content: bytes, mime_type: str, context: str) -> tuple[dict[str, Any], dict[str, int], str]:
        from app.services.ai import AIError

        try:
            raw = await self.provider.complete_json(system=SYSTEM_PROMPT, text=context, file=(content, mime_type), schema=AI_SCHEMA)
        except AIError as exc:
            raise ExtractionError(str(exc)) from exc
        return raw, {}, self.provider.label


# ───────────────────────── Ableitung: Vorschlag ─────────────────────────

def suggest(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Leitet aus Rohdaten einen Belegvorschlag mit Steuerfall ab (feste Regeln, keine KI-Entscheidung)."""
    warnings: list[str] = []
    country = (raw.get("supplier_country") or "DE").upper()[:2]
    currency = (raw.get("currency") or "EUR").upper()
    net, tax, gross = _dec(raw.get("net_amount")), _dec(raw.get("tax_amount")) or D("0.00"), _dec(raw.get("gross_amount"))
    groups = raw.get("groups") or []
    rate_set: set[Decimal] = {r for r in (_dec(x) for x in raw.get("tax_rates_percent") or []) if r is not None}
    rate_set |= {D(g["rate"]) for g in groups if g.get("rate") is not None and g.get("tax")}
    rates = sorted(rate_set)
    rc = bool(raw.get("reverse_charge_note")) or any(g.get("category") == "AE" for g in groups)
    category = raw.get("category") if raw.get("category") in CATEGORY_CODES else "other"

    if net is None and gross is not None:
        net = gross - tax
    if gross is None and net is not None:
        gross = net + tax
    if net is not None and gross is not None and abs(net + tax - gross) > D("0.02"):
        warnings.append(f"Rechenprüfung: Netto {net} + Steuer {tax} ≠ Brutto {gross} – Beträge prüfen.")
    if currency != "EUR":
        warnings.append(f"Fremdwährung {currency}: Beträge mit dem EZB-Referenzkurs des Rechnungsdatums in Euro umrechnen.")
    if raw.get("type_code") in ("381", "396"):
        warnings.append("Gutschrift/Rechnungskorrektur – Beträge negativ erfassen.")
    if len([r for r in rates if r and r > 0]) > 1:
        warnings.append("Mehrere Steuersätze – Beleg bitte je Steuersatz aufteilen (ein Beleg je Satz).")

    if rc:
        tax_case = "IN_13B_EU_SERVICE" if country in EU else "IN_13B_DRITTLAND_SERVICE" if country != "DE" else "IN_DE_19"
        if country == "DE":
            warnings.append("Reverse-Charge-Hinweis bei deutschem Lieferanten (z. B. Bauleistung § 13b Abs. 2 Nr. 4) – "
                            "Steuerberater fragen.")
        if tax:
            warnings.append("Reverse Charge, aber Steuer ausgewiesen – Beleg prüfen.")
        tax = D("0.00")
        gross = net
    elif raw.get("small_business_note") or (tax == 0 and country == "DE"):
        tax_case = "IN_OHNE_VST"
    elif country != "DE":
        # Ausländische Umsatzsteuer ist im deutschen Verfahren nicht als Vorsteuer abziehbar → Aufwand brutto
        tax_case = "IN_OHNE_VST"
        if tax:
            warnings.append(f"Ausländische Umsatzsteuer ({country}) ist nicht als Vorsteuer abziehbar – ggf. Vergütungsverfahren "
                            "oder Anbieter um Rechnung mit Reverse Charge bitten (eigene USt-IdNr hinterlegen).")
    elif rates and rates[-1] == D("7.00"):
        tax_case = "IN_DE_7"
    else:
        tax_case = "IN_DE_19"
    if category == "hospitality" and tax_case in ("IN_DE_19", "IN_DE_7"):
        tax_case = "IN_BEWIRTUNG"
        if rates and rates[-1] == D("7.00"):
            warnings.append("Bewirtung mit 7 % – Steuerfall Bewirtung rechnet mit 19 %; Steuerbetrag prüfen.")

    confidence = raw.get("confidence") if source == "ai" else 1.0
    if source == "ai" and isinstance(confidence, int | float) and confidence < 0.7:
        warnings.append("Geringe Erkennungssicherheit – alle Felder sorgfältig prüfen.")
    if source == "ai" and raw.get("is_invoice") is False:
        warnings.append("Das Dokument scheint keine Rechnung zu sein.")
    if raw.get("notes"):
        warnings.append(f"Hinweis der Erkennung: {raw['notes']}")
    description = (raw.get("description") or raw.get("supplier_name") or "").strip()[:500]
    return {
        "source": source, "supplier_name": (raw.get("supplier_name") or "").strip()[:200] or None,
        "supplier_vat_id": (raw.get("supplier_vat_id") or "").replace(" ", "").upper() or None, "supplier_country": country,
        "invoice_number": (raw.get("invoice_number") or None), "receipt_date": raw.get("receipt_date") or None,
        "service_date": raw.get("service_date") or None, "description": description or "Beleg", "category": category,
        "tax_case": tax_case, "net_amount": str(net) if net is not None else None, "tax_amount": str(tax),
        "gross_amount": str(gross) if gross is not None else None, "currency": currency,
        "already_paid": bool(raw.get("already_paid")), "confidence": confidence, "warnings": warnings,
    }


async def _match_supplier(conn: AsyncConnection, tenant_id: UUID, s: dict[str, Any]) -> UUID | None:
    if s.get("supplier_vat_id"):
        sid = await conn.scalar(text("SELECT id FROM suppliers WHERE tenant_id = :t AND upper(vat_id) = :v AND archived_at IS NULL"),
                                {"t": tenant_id, "v": s["supplier_vat_id"]})
        if sid:
            return sid  # type: ignore[no-any-return]
    if s.get("supplier_name"):
        sid = await conn.scalar(text("SELECT id FROM suppliers WHERE tenant_id = :t AND lower(name) = lower(:n) AND archived_at IS NULL"),
                                {"t": tenant_id, "n": s["supplier_name"]})
        return sid  # type: ignore[no-any-return]
    return None


async def supplier_from_text(conn: AsyncConnection, tenant_id: UUID, content: bytes, own_vat: str | None) -> dict[str, Any] | None:
    """Regelbasierte Ergänzung, wenn die KI den Lieferanten nicht erkannt hat: bekannte USt-IdNr. oder bekannter
    Lieferantenname im Text des (digitalen) PDFs. Die eigene USt-IdNr. wird ignoriert."""
    from app.services.ai import pdf_to_text_and_images

    text_, _ = pdf_to_text_and_images(content, max_pages=2)
    if not text_:
        return None
    upper = text_.upper().replace(" ", "")
    rows = (await conn.execute(text(
        "SELECT id, name, vat_id, country_code FROM suppliers WHERE tenant_id = :t AND archived_at IS NULL"),
        {"t": tenant_id})).mappings().all()
    for r in rows:
        if r["vat_id"] and r["vat_id"].upper() != (own_vat or "").upper() and r["vat_id"].upper() in upper:
            return dict(r)
    lower = " ".join(text_.lower().split())
    hits = [r for r in rows if len(r["name"]) >= 4 and r["name"].lower() in lower]
    if len(hits) == 1:
        return dict(hits[0])
    return None


async def extract_file(conn: AsyncConnection, extractor: AIExtractor | None, tenant_id: UUID, file_id: UUID, actor: str,
                       company: str) -> dict[str, Any]:
    from app.services.receipts import get_file

    cached = (await conn.execute(text("SELECT source, result FROM receipt_extractions WHERE file_id = :id AND tenant_id = :t"),
                                 {"id": file_id, "t": tenant_id})).one_or_none()
    if cached is not None:
        suggestion = dict(cached.result)
        suggestion["cached"] = True
    else:
        f = await get_file(conn, tenant_id, file_id)
        content = bytes(f["content"])
        xml = content if f["mime_type"] == "application/xml" else embedded_xml(content) if f["mime_type"] == "application/pdf" else None
        tokens: dict[str, int] = {}
        model = None
        if xml is not None:
            suggestion = suggest(parse_einvoice(xml), source="einvoice")
        else:
            if extractor is None:
                raise ExtractionNotConfigured("KI-Belegerkennung ist nicht eingerichtet (lokale KI: OLLAMA_URL, alternativ "
                                              "ANTHROPIC_API_KEY) – bitte manuell erfassen")
            raw, tokens, model = await extractor.extract(content, f["mime_type"],
                                                         f"Rechnungsempfänger (wir): {company}. Bitte die Rechnungsdaten erfassen.")
            suggestion = suggest(raw, source="ai")
            if not suggestion.get("supplier_name") and f["mime_type"] == "application/pdf":
                own = await conn.scalar(text("SELECT vat_id FROM company_profiles WHERE tenant_id = :t ORDER BY valid_from DESC LIMIT 1"),
                                        {"t": tenant_id})
                known = await supplier_from_text(conn, tenant_id, content, own)
                if known:
                    suggestion["supplier_name"] = known["name"]
                    suggestion["supplier_vat_id"] = known["vat_id"]
                    suggestion["supplier_country"] = known["country_code"]
                    suggestion["warnings"] = [*suggestion.get("warnings", []), "Lieferant anhand des Belegtexts zugeordnet."]
        await conn.execute(text(
            "INSERT INTO receipt_extractions (file_id, tenant_id, source, model, result, input_tokens, output_tokens, created_by) "
            "VALUES (:f, :t, :s, :m, CAST(:r AS jsonb), :i, :o, :a)"),
            {"f": file_id, "t": tenant_id, "s": suggestion["source"], "m": model, "r": json.dumps(suggestion),
             "i": tokens.get("input"), "o": tokens.get("output"), "a": actor})
        suggestion["cached"] = False
    suggestion["supplier_id"] = await _match_supplier(conn, tenant_id, suggestion)
    suggestion["expense_account"] = None
    suggestion["supplier_known"] = False
    if suggestion["supplier_id"] is not None:
        rule = (await conn.execute(text(
            "SELECT default_category, default_tax_case, default_account, needs_review FROM suppliers WHERE id = :id"),
            {"id": suggestion["supplier_id"]})).mappings().one()
        if not rule["needs_review"] and rule["default_account"]:
            # Lieferant mit bewusst festgelegter Regel (Standardkonto): Kontierung aus der Regel, nicht aus der KI-Schätzung.
            # Ohne Standardkonto bleiben die erkannten Werte stehen (Standardwerte dürfen z. B. Reverse Charge nicht überschreiben).
            suggestion["supplier_known"] = True
            suggestion["category"] = rule["default_category"]
            suggestion["expense_account"] = rule["default_account"]
            if suggestion["source"] == "ai":  # E-Rechnungen (XML) behalten ihren exakt gelesenen Steuerfall
                suggestion["tax_case"] = rule["default_tax_case"]
                if rule["default_tax_case"].startswith(("IN_13B", "IN_IG")):
                    suggestion["tax_amount"] = "0.00"
    if suggestion.get("receipt_date"):
        try:
            if dt.date.fromisoformat(suggestion["receipt_date"]) > dt.date.today() + dt.timedelta(days=1):  # noqa: DTZ011
                suggestion["warnings"] = [*suggestion["warnings"], "Rechnungsdatum liegt in der Zukunft – prüfen."]
        except ValueError:
            suggestion["receipt_date"] = None
    return suggestion
