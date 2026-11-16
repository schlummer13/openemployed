"""XRechnung (CIUS des EN 16931 für die öffentliche Verwaltung, Standard 3.0) und Leitweg-ID.

Die XRechnung wird als CII-Syntax erzeugt – derselbe Datensatz wie die ZUGFeRD-Rechnung, ergänzt um die deutschen
Pflichtangaben (BR-DE): Leitweg-ID als Käuferreferenz (BT-10), Kontakt des Verkäufers (BT-41 bis BT-43) sowie
elektronische Adressen von Verkäufer und Käufer (BT-34, BT-49). Übermittlung: Upload/E-Mail an ZRE, OZG-RE oder
Landesportale bzw. Peppol über einen Access Point.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

GUIDELINE_XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PEPPOL_PROCESS = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
LEITWEG = re.compile(r"^(\d{2,12})(?:-([A-Za-z0-9]{1,30}))?-(\d{2})$")
PEPPOL_ID = re.compile(r"^(\d{4}):([A-Za-z0-9._:\-]{1,50})$")


def leitweg_problem(value: str) -> str | None:
    """Prüft Aufbau und Prüfziffer (ISO 7064 Mod 97-10) einer Leitweg-ID, z. B. 991-12345-67 oder 05315000-SV001-41."""
    m = LEITWEG.fullmatch(value.strip())
    if not m:
        return "Leitweg-ID: Grobadressierung (2–12 Ziffern) - optional Feinadressierung - zweistellige Prüfziffer"
    body = (m.group(1) + (m.group(2) or "")).upper()
    digits = "".join(str(int(c, 36)) for c in body)
    if 98 - (int(digits + "00") % 97) != int(m.group(3)):
        return "Leitweg-ID: Prüfziffer stimmt nicht"
    return None


def leitweg_with_check(prefix: str) -> str:
    """Hilfsfunktion (Tests/Beispiele): ergänzt die Prüfziffer zu „Grob-Fein“."""
    body = prefix.replace("-", "").upper()
    check = 98 - int("".join(str(int(c, 36)) for c in body) + "00") % 97
    return f"{prefix}-{check:02d}"


def customer_problems(c: dict[str, Any]) -> list[str]:
    out = []
    if c.get("einvoice_format") == "xrechnung":
        if not c.get("buyer_reference"):
            out.append("Leitweg-ID (Käuferreferenz) fehlt")
        elif re.fullmatch(r"[\d\-A-Za-z]+", c["buyer_reference"] or "") and c["buyer_reference"][0].isdigit():
            p = leitweg_problem(c["buyer_reference"])
            if p:
                out.append(p)
    if c.get("peppol_id") and not PEPPOL_ID.fullmatch(c["peppol_id"]):
        out.append("Peppol-ID im Format 0204:991-12345-67 (Schema:Kennung)")
    return out


def to_xrechnung(cii: bytes, snapshot: dict[str, Any]) -> bytes:
    """Wandelt die EN-16931-CII in eine XRechnung 3.0 (gleiche Inhalte, deutsche Zusatzangaben)."""
    ns = {"rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
          "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}
    root = etree.fromstring(cii)

    def q(prefix: str, name: str) -> str:
        return f"{{{ns[prefix]}}}{name}"

    seller, buyer = snapshot["seller"], snapshot["buyer"]
    context = root.find("rsm:ExchangedDocumentContext", ns)
    guideline = context.find("ram:GuidelineSpecifiedDocumentContextParameter", ns)
    guideline.find("ram:ID", ns).text = GUIDELINE_XRECHNUNG
    process = etree.Element(q("ram", "BusinessProcessSpecifiedDocumentContextParameter"))
    etree.SubElement(process, q("ram", "ID")).text = PEPPOL_PROCESS
    guideline.addprevious(process)

    agreement = root.find("rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement", ns)
    if agreement.find("ram:BuyerReference", ns) is None:
        raise ValueError("XRechnung: Leitweg-ID (BT-10) fehlt beim Kunden")
    seller_party = agreement.find("ram:SellerTradeParty", ns)
    # BT-41..43: Kontakt (Pflicht BR-DE-2/5/6/7); Reihenfolge laut Schema vor der Anschrift
    contact = etree.Element(q("ram", "DefinedTradeContact"))
    etree.SubElement(contact, q("ram", "PersonName")).text = (seller.get("managing_directors") or [seller["legal_name"]])[0]
    phone = etree.SubElement(contact, q("ram", "TelephoneUniversalCommunication"))
    if not seller.get("phone") or not seller.get("email"):
        raise ValueError("XRechnung: Telefon und E-Mail im Firmenprofil sind Pflicht (BR-DE-6/7)")
    etree.SubElement(phone, q("ram", "CompleteNumber")).text = seller["phone"]
    mail = etree.SubElement(contact, q("ram", "EmailURIUniversalCommunication"))
    etree.SubElement(mail, q("ram", "URIID")).text = seller["email"]
    seller_party.find("ram:PostalTradeAddress", ns).addprevious(contact)
    # BT-49: elektronische Adresse des Käufers – Peppol-ID (z. B. Leitweg-ID unter Schema 0204) oder E-Mail
    buyer_party = agreement.find("ram:BuyerTradeParty", ns)
    existing = buyer_party.find("ram:URIUniversalCommunication", ns)
    if existing is not None:
        buyer_party.remove(existing)
    comm = etree.Element(q("ram", "URIUniversalCommunication"))
    pid = buyer.get("peppol_id")
    if pid and PEPPOL_ID.fullmatch(pid):
        scheme, ident = pid.split(":", 1)
        etree.SubElement(comm, q("ram", "URIID"), schemeID=scheme).text = ident
    else:
        etree.SubElement(comm, q("ram", "URIID"), schemeID="EM").text = buyer["email"]
    tax = buyer_party.find("ram:SpecifiedTaxRegistration", ns)
    if tax is not None:
        tax.addprevious(comm)
    else:
        buyer_party.append(comm)
    result: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return result


def check_xrechnung(xml: bytes) -> list[str]:
    """Pflichtangaben der XRechnung (Auszug der BR-DE-Regeln) zusätzlich zur EN-16931-Prüfung."""
    ns = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}
    root = etree.fromstring(xml)
    problems = []
    rules = {
        "BR-DE-1 Zahlungsanweisungen": ".//ram:SpecifiedTradeSettlementPaymentMeans",
        "BR-DE-2 Verkäuferkontakt": ".//ram:SellerTradeParty/ram:DefinedTradeContact",
        "BR-DE-15 Leitweg-ID": ".//ram:ApplicableHeaderTradeAgreement/ram:BuyerReference",
        "BR-DE-3 Ort des Verkäufers": ".//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CityName",
        "BT-34 elektronische Adresse Verkäufer": ".//ram:SellerTradeParty/ram:URIUniversalCommunication/ram:URIID",
        "BT-49 elektronische Adresse Käufer": ".//ram:BuyerTradeParty/ram:URIUniversalCommunication/ram:URIID",
        "BR-DE-18 Zahlungsbedingungen": ".//ram:SpecifiedTradePaymentTerms",
    }
    for rule, path in rules.items():
        el = root.find(path, ns)
        if el is None or (len(el) == 0 and not (el.text or "").strip()):
            problems.append(rule)
    guide = root.find(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", ns)
    if guide is None or guide.text != GUIDELINE_XRECHNUNG:
        problems.append("BR-DE-21 Spezifikationskennung")
    return problems
