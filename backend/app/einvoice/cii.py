"""E-Rechnung als UN/CEFACT Cross Industry Invoice (CII), Profil EN 16931 (Factur-X / ZUGFeRD 2.x „EN 16931“).

Eingabe ist ausschließlich die festgeschriebene Fassung (Snapshot) – so stimmen PDF, XML und Ledger überein.

Stornorechnungen werden als Gutschrift im Sinne von EN 16931 (TypeCode 381) mit positiven Beträgen und Verweis
auf die Originalrechnung (BG-3) ausgegeben; die Belastungsrichtung ergibt sich aus dem Dokumenttyp.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from lxml import etree

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
GUIDELINE_EN16931 = "urn:cen.eu:en16931:2017"
TYPE_INVOICE = "380"
TYPE_CREDIT_NOTE = "381"


def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def _el(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    prefix, name = tag.split(":")
    node = etree.SubElement(parent, _q(prefix, name), attrib=attrs or None)
    if text is not None:
        node.text = text
    return node


def _date(parent: etree._Element, tag: str, value: str, *, qualified: bool = False) -> None:
    holder = _el(parent, tag)
    _el(holder, "qdt:DateTimeString" if qualified else "udt:DateTimeString",
        dt.date.fromisoformat(value).strftime("%Y%m%d"), format="102")


def _amount(value: Decimal | str, sign: int = 1, places: int = 2) -> str:
    d = Decimal(value) * sign
    return f"{d:.{places}f}"


def _price(value: Decimal | str) -> str:
    """Preis mit 2–4 Nachkommastellen (nicht mehr als nötig)."""
    d = Decimal(value)
    text = f"{d:.4f}".rstrip("0")
    head, _, tail = text.partition(".")
    return f"{head}.{tail.ljust(2, '0')}"


def _quantity(value: Decimal | str, sign: int) -> str:
    d = Decimal(value) * sign
    text = f"{d:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _rate(value: str) -> str:
    return f"{Decimal(value):.2f}"


def _party(parent: etree._Element, tag: str, *, name: str, street: str, postal_code: str, city: str,
           country: str, email: str | None, vat_id: str | None = None, tax_number: str | None = None,
           legal_registration: str | None = None) -> None:
    party = _el(parent, tag)
    _el(party, "ram:Name", name)
    if legal_registration:
        # BT-30: Handelsregisternummer – erfüllt BR-CO-26, wenn keine USt-IdNr vorhanden ist
        _el(_el(party, "ram:SpecifiedLegalOrganization"), "ram:ID", legal_registration)
    address = _el(party, "ram:PostalTradeAddress")
    _el(address, "ram:PostcodeCode", postal_code)
    _el(address, "ram:LineOne", street)
    _el(address, "ram:CityName", city)
    _el(address, "ram:CountryID", country)
    if email:
        comm = _el(party, "ram:URIUniversalCommunication")
        _el(comm, "ram:URIID", email, schemeID="EM")
    if vat_id:
        reg = _el(party, "ram:SpecifiedTaxRegistration")
        _el(reg, "ram:ID", vat_id, schemeID="VA")
    if tax_number:
        reg = _el(party, "ram:SpecifiedTaxRegistration")
        _el(reg, "ram:ID", tax_number, schemeID="FC")


def build_cii(number: str, snapshot: dict[str, Any]) -> bytes:
    """Erzeugt das CII-XML (UTF-8) zu einer festgeschriebenen Rechnung."""
    is_credit = snapshot["kind"] == "cancellation"
    sign = -1 if is_credit else 1  # Storno: negative Snapshot-Werte → positive Gutschriftwerte
    seller = snapshot["seller"]
    buyer = snapshot["buyer"]
    totals = snapshot["totals"]

    root = etree.Element(_q("rsm", "CrossIndustryInvoice"), nsmap=NS)

    context = _el(root, "rsm:ExchangedDocumentContext")
    guideline = _el(context, "ram:GuidelineSpecifiedDocumentContextParameter")
    _el(guideline, "ram:ID", GUIDELINE_EN16931)

    doc = _el(root, "rsm:ExchangedDocument")
    _el(doc, "ram:ID", number)
    _el(doc, "ram:TypeCode", TYPE_CREDIT_NOTE if is_credit else TYPE_INVOICE)
    _date(doc, "ram:IssueDateTime", snapshot["issue_date"])
    notes = [n for n in (snapshot.get("intro_text"), *snapshot.get("notes", []), snapshot.get("closing_text")) if n]
    if snapshot.get("cancellation_reason"):
        notes.append(f"Grund: {snapshot['cancellation_reason']}")
    for note in notes:
        included = _el(doc, "ram:IncludedNote")
        _el(included, "ram:Content", note)
    # Pflichtangaben nach § 35a GmbHG als Hinweis mit Code REG
    registration = [seller["legal_name"], f"{seller['street']}, {seller['postal_code']} {seller['city']}"]
    if seller.get("register_court"):
        registration.append(f"{seller['register_court']} {seller.get('register_number') or ''}".strip())
    registration.append("Geschäftsführer: " + ", ".join(seller["managing_directors"]))
    reg_note = _el(doc, "ram:IncludedNote")
    _el(reg_note, "ram:Content", " · ".join(registration))
    _el(reg_note, "ram:SubjectCode", "REG")

    transaction = _el(root, "rsm:SupplyChainTradeTransaction")
    for line in snapshot["lines"]:
        item = _el(transaction, "ram:IncludedSupplyChainTradeLineItem")
        _el(_el(item, "ram:AssociatedDocumentLineDocument"), "ram:LineID", str(line["position"]))
        _el(_el(item, "ram:SpecifiedTradeProduct"), "ram:Name", line["description"])
        agreement = _el(item, "ram:SpecifiedLineTradeAgreement")
        _el(_el(agreement, "ram:NetPriceProductTradePrice"), "ram:ChargeAmount", _price(line["unit_price_net"]))
        delivery = _el(item, "ram:SpecifiedLineTradeDelivery")
        _el(delivery, "ram:BilledQuantity", _quantity(line["quantity"], sign), unitCode=line["unit_code"])
        settlement = _el(item, "ram:SpecifiedLineTradeSettlement")
        tax = _el(settlement, "ram:ApplicableTradeTax")
        _el(tax, "ram:TypeCode", "VAT")
        _el(tax, "ram:CategoryCode", line["category"])
        if line["category"] != "O":  # BR-O-05: „nicht steuerbar“ ohne Steuersatz
            _el(tax, "ram:RateApplicablePercent", _rate(line["rate"]))
        summation = _el(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation")
        _el(summation, "ram:LineTotalAmount", _amount(line["line_net"], sign))

    # BR-O-02..04: Rechnungen nur mit nicht steuerbaren Umsätzen (Kategorie O) ohne USt-IdNr. von Verkäufer und Käufer
    only_outside = all(g["category"] == "O" for g in snapshot["tax_breakdown"])
    agreement = _el(transaction, "ram:ApplicableHeaderTradeAgreement")
    if buyer.get("buyer_reference"):
        _el(agreement, "ram:BuyerReference", buyer["buyer_reference"])
    _party(agreement, "ram:SellerTradeParty", name=seller["legal_name"], street=seller["street"],
           postal_code=seller["postal_code"], city=seller["city"], country=seller["country_code"],
           email=seller.get("email"), vat_id=None if only_outside else seller.get("vat_id"), tax_number=seller.get("tax_number"),
           legal_registration=" ".join(p for p in (seller.get("register_number"), seller.get("register_court")) if p)
           or None)
    _party(agreement, "ram:BuyerTradeParty", name=buyer["name"], street=buyer["street"],
           postal_code=buyer["postal_code"], city=buyer["city"], country=buyer["country_code"],
           email=buyer.get("email"), vat_id=None if only_outside else buyer.get("vat_id"))

    delivery = _el(transaction, "ram:ApplicableHeaderTradeDelivery")
    period = snapshot["service_period"]
    delivery_date = period.get("to") or period.get("from")
    if delivery_date:
        # BT-72: Leistungsdatum (Ende des Zeitraums); vermeidet zudem ein leeres Pflichtelement
        event = _el(delivery, "ram:ActualDeliverySupplyChainEvent")
        _date(event, "ram:OccurrenceDateTime", delivery_date)

    settlement = _el(transaction, "ram:ApplicableHeaderTradeSettlement")
    _el(settlement, "ram:PaymentReference", number)
    foreign = snapshot["currency"] != "EUR" and snapshot.get("tax_eur") is not None
    if foreign:
        _el(settlement, "ram:TaxCurrencyCode", "EUR")  # BT-6: Umsatzsteuer in Euro (§ 14 Abs. 4 Nr. 8 UStG)
    _el(settlement, "ram:InvoiceCurrencyCode", snapshot["currency"])
    if seller.get("iban") and not is_credit:
        means = _el(settlement, "ram:SpecifiedTradeSettlementPaymentMeans")
        _el(means, "ram:TypeCode", "58")  # SEPA-Überweisung
        account = _el(means, "ram:PayeePartyCreditorFinancialAccount")
        _el(account, "ram:IBANID", seller["iban"])
    for group in snapshot["tax_breakdown"]:
        tax = _el(settlement, "ram:ApplicableTradeTax")
        _el(tax, "ram:CalculatedAmount", _amount(group["tax_amount"], sign))
        _el(tax, "ram:TypeCode", "VAT")
        if group.get("exemption_reason"):
            _el(tax, "ram:ExemptionReason", group["exemption_reason"])
        _el(tax, "ram:BasisAmount", _amount(group["taxable_amount"], sign))
        _el(tax, "ram:CategoryCode", group["category"])
        if group["category"] != "O":  # BR-O-10
            _el(tax, "ram:RateApplicablePercent", _rate(group["rate"]))
    if period.get("from") and period.get("to"):
        billing = _el(settlement, "ram:BillingSpecifiedPeriod")
        _date(billing, "ram:StartDateTime", period["from"])
        _date(billing, "ram:EndDateTime", period["to"])
    if not is_credit:
        terms = _el(settlement, "ram:SpecifiedTradePaymentTerms")
        due = dt.date.fromisoformat(snapshot["due_date"])
        _el(terms, "ram:Description", f"Zahlbar ohne Abzug bis {due:%d.%m.%Y}")
        _date(terms, "ram:DueDateDateTime", snapshot["due_date"])
    summary = _el(settlement, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(summary, "ram:LineTotalAmount", _amount(totals["net"], sign))
    _el(summary, "ram:TaxBasisTotalAmount", _amount(totals["net"], sign))
    _el(summary, "ram:TaxTotalAmount", _amount(totals["tax"], sign), currencyID=snapshot["currency"])
    if foreign:
        _el(summary, "ram:TaxTotalAmount", _amount(snapshot["tax_eur"], sign), currencyID="EUR")  # BT-111
    _el(summary, "ram:GrandTotalAmount", _amount(totals["gross"], sign))
    _el(summary, "ram:DuePayableAmount", _amount(totals["gross"], sign))
    if is_credit and snapshot.get("cancels"):
        ref = _el(settlement, "ram:InvoiceReferencedDocument")
        _el(ref, "ram:IssuerAssignedID", snapshot["cancels"]["number"])
        _date(ref, "ram:FormattedIssueDateTime", snapshot["cancels"]["issue_date"], qualified=True)

    result: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return result
