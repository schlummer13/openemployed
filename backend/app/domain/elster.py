"""ELSTER-Datensatz der Umsatzsteuer-Voranmeldung (ElsterAnmeldung / UStVA) – Erzeugung ohne ERiC.

Die Übertragung selbst erfolgt ausschließlich über die offizielle ERiC-Bibliothek der Finanzverwaltung (Plausibilitäts-
prüfung, Verschlüsselung, Versand). Diese Datei erzeugt nur den Nutzdatensatz; ERiC prüft ihn vor jedem Versand.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_DOWN, Decimal
from xml.sax.saxutils import escape

TEST_MARKER = "700000004"  # Testmerker: Fall wird vom Finanzamt nicht verarbeitet

# Bundeseinheitliches 13-stelliges Format: Präfix je Land + Finanzamt (FFF) + 0 + Bezirk/Unterscheidung + Prüfziffer.
# Eingabe im „Vordruckformat“ des Landes (Ziffern ohne Trennzeichen) → (Ländernummer-Präfix, Muster der Eingabeziffern)
_STATE_RULES: dict[str, tuple[str, int]] = {
    "BW": ("28", 10), "BY": ("9", 11), "BE": ("11", 10), "BB": ("3", 11), "HB": ("24", 10), "HH": ("22", 10),
    "HE": ("26", 11), "MV": ("4", 11), "NI": ("23", 10), "NW": ("5", 11), "RP": ("27", 10), "SL": ("1", 11),
    "SN": ("3", 11), "ST": ("3", 11), "SH": ("21", 10), "TH": ("4", 11),
}


class ElsterError(ValueError):
    pass


def elster_tax_number(tax_number: str, state: str) -> str:
    """Steuernummer (z. B. NRW „103/5734/0815“) → 13-stelliges ELSTER-Format (z. B. „5103057340815“)."""
    digits = re.sub(r"\D", "", tax_number or "")
    if len(digits) == 13:
        return digits
    rule = _STATE_RULES.get(state)
    if rule is None:
        raise ElsterError(f"Unbekanntes Bundesland {state}")
    prefix, length = rule
    if len(digits) != length:
        raise ElsterError(f"Steuernummer für {state} muss {length} Ziffern haben (ohne Trennzeichen)")
    if length == 10:            # FF BBB UUUUP → Präfix(2) FF 0 BBB UUUUP
        result = prefix + digits[:2] + "0" + digits[2:]
    elif state == "NW":         # FFF BBBB BBBP → 5 FFF 0 BBBBBBBP
        result = prefix + digits[:3] + "0" + digits[3:]
    elif state == "HE":         # 0FF BBB UUUUP → 26 FF 0 BBB UUUUP
        result = prefix + digits[1:3] + "0" + digits[3:]
    elif state in ("SL", "SN", "ST", "TH"):  # 0FF/2FF/1FF BBB UUUUP → Präfix + FFF 0 BBB UUUUP (erste Ziffer gehört zum Amt)
        result = prefix + digits[:3] + "0" + digits[3:]
        if state == "SL":
            result = prefix + "0" + digits[1:3] + "0" + digits[3:]
    else:                       # FFF BBB UUUUP → Präfix FFF 0 BBB UUUUP
        result = prefix + digits[:3] + "0" + digits[3:]
    if len(result) != 13:
        raise ElsterError("Steuernummer konnte nicht umgerechnet werden")
    return result


def zeitraum(spec: str) -> tuple[str, str]:
    """„2026-11“ → („2026“, „11“); „2026-Q4“ → („2026“, „44“) (Quartale 41–44)."""
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2]|Q[1-4])", spec)
    if not m:
        raise ElsterError("Zeitraum im Format JJJJ-MM oder JJJJ-Q1 … Q4")
    return m.group(1), ("4" + m.group(2)[1]) if m.group(2).startswith("Q") else m.group(2)


def _amount(kz: str, value: Decimal) -> str:
    """Bemessungsgrundlagen ganze Euro (abgerundet), Steuerbeträge mit Cent."""
    if kz in ("81", "86", "89", "46", "84", "41", "43", "21", "45"):
        return str(value.to_integral_value(rounding=ROUND_DOWN))
    return f"{value.quantize(Decimal('0.01')):.2f}"


def ustva_xml(*, spec: str, lines: list[dict[str, object]], tax_number: str, state: str, company: dict[str, str],
              hersteller_id: str, test: bool, created: dt.date, product_version: str = "1.0") -> str:
    """Vollständiger ELSTER-Datensatz (TransferHeader + Nutzdaten) der UStVA aus den Kennzahlen der Voranmeldung."""
    year, period = zeitraum(spec)
    stnr = elster_tax_number(tax_number, state)
    kz_xml = []
    for ln in lines:
        kz = str(ln["kz"])
        if kz in ("41", "21", "45", "81", "86", "89", "46", "84") and ln.get("base") is not None:
            kz_xml.append(f"<Kz{kz}>{_amount(kz, Decimal(str(ln['base'])))}</Kz{kz}>")
        elif kz in ("47", "85", "66", "61", "62", "67", "39", "83") and ln.get("tax") is not None:
            kz_xml.append(f"<Kz{kz}>{_amount(kz, Decimal(str(ln['tax'])))}</Kz{kz}>")
    marker = f"<Testmerker>{TEST_MARKER}</Testmerker>" if test else ""
    e = {k: escape(v or "") for k, v in company.items()}
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Elster xmlns="http://www.elster.de/elsterxml/schema/v11">'
        '<TransferHeader version="11"><Verfahren>ElsterAnmeldung</Verfahren><DatenArt>UStVA</DatenArt><Vorgang>send-Auth</Vorgang>'
        f"{marker}<HerstellerID>{escape(hersteller_id)}</HerstellerID><DatenLieferant>{e['name']}</DatenLieferant>"
        "<Datei><Verschluesselung>CMSEncryptedData</Verschluesselung><Kompression>GZIP</Kompression><TransportSchluessel/></Datei>"
        "</TransferHeader>"
        '<DatenTeil><Nutzdatenblock><NutzdatenHeader version="11"><NutzdatenTicket>1</NutzdatenTicket>'
        f'<Empfaenger id="F">{stnr[:4]}</Empfaenger><Hersteller><ProduktName>OpenEmployed</ProduktName>'
        f"<ProduktVersion>{escape(product_version)}</ProduktVersion></Hersteller></NutzdatenHeader>"
        f'<Nutzdaten><Anmeldungssteuern xmlns="http://finkonsens.de/elster/elsteranmeldung/ustva/v{year}" version="{year}">'
        f"<DatenLieferant><Name>{e['name']}</Name><Strasse>{e['street']}</Strasse><PLZ>{e['postal_code']}</PLZ><Ort>{e['city']}</Ort>"
        f"</DatenLieferant><Erstellungsdatum>{created:%Y%m%d}</Erstellungsdatum>"
        f"<Steuerfall><Umsatzsteuervoranmeldung><Jahr>{year}</Jahr><Zeitraum>{period}</Zeitraum><Steuernummer>{stnr}</Steuernummer>"
        f"<Kz09>{escape(hersteller_id)}</Kz09>{''.join(kz_xml)}</Umsatzsteuervoranmeldung></Steuerfall>"
        "</Anmeldungssteuern></Nutzdaten></Nutzdatenblock></DatenTeil></Elster>"
    )


def lsta_xml(*, period: str, tax_number: str, state: str, company: dict[str, str], employees: int, lst: Decimal,
             flat_lst: Decimal, solz: Decimal, church_ev: Decimal, church_rk: Decimal, hersteller_id: str, test: bool,
             created: dt.date, product_version: str = "1.0") -> str:
    """Lohnsteuer-Anmeldung (ElsterAnmeldung / LStA) für einen Monat (JJJJ-MM)."""
    year, month = zeitraum(period)
    stnr = elster_tax_number(tax_number, state)
    e = {k: escape(str(v or "")) for k, v in company.items()}
    total = lst + flat_lst + solz + church_ev + church_rk
    kz = [f"<Kz86>{employees}</Kz86>", f"<Kz42>{_amount('42', lst)}</Kz42>"]
    if flat_lst:
        kz.append(f"<Kz41>{_amount('42', flat_lst)}</Kz41>")
    kz += [f"<Kz49>{_amount('49', solz)}</Kz49>"]
    if church_ev:
        kz.append(f"<Kz61>{_amount('61', church_ev)}</Kz61>")
    if church_rk:
        kz.append(f"<Kz62>{_amount('62', church_rk)}</Kz62>")
    kz.append(f"<Kz83>{_amount('83', total)}</Kz83>")
    marker = f"<Testmerker>{TEST_MARKER}</Testmerker>" if test else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Elster xmlns="http://www.elster.de/elsterxml/schema/v11">'
        '<TransferHeader version="11"><Verfahren>ElsterAnmeldung</Verfahren><DatenArt>LStA</DatenArt><Vorgang>send-Auth</Vorgang>'
        f"{marker}<HerstellerID>{escape(hersteller_id)}</HerstellerID><DatenLieferant>{e['name']}</DatenLieferant>"
        "<Datei><Verschluesselung>CMSEncryptedData</Verschluesselung><Kompression>GZIP</Kompression><TransportSchluessel/></Datei>"
        "</TransferHeader>"
        '<DatenTeil><Nutzdatenblock><NutzdatenHeader version="11"><NutzdatenTicket>1</NutzdatenTicket>'
        f'<Empfaenger id="F">{stnr[:4]}</Empfaenger><Hersteller><ProduktName>OpenEmployed</ProduktName>'
        f"<ProduktVersion>{escape(product_version)}</ProduktVersion></Hersteller></NutzdatenHeader>"
        f'<Nutzdaten><Anmeldungssteuern xmlns="http://finkonsens.de/elster/elsteranmeldung/lsta/v{year}" version="{year}">'
        f"<DatenLieferant><Name>{e['name']}</Name><Strasse>{e['street']}</Strasse><PLZ>{e['postal_code']}</PLZ><Ort>{e['city']}</Ort>"
        f"</DatenLieferant><Erstellungsdatum>{created:%Y%m%d}</Erstellungsdatum>"
        f"<Steuerfall><Lohnsteueranmeldung><Jahr>{year}</Jahr><Zeitraum>{month}</Zeitraum><Steuernummer>{stnr}</Steuernummer>"
        f"{''.join(kz)}</Lohnsteueranmeldung></Steuerfall>"
        "</Anmeldungssteuern></Nutzdaten></Nutzdatenblock></DatenTeil></Elster>"
    )
