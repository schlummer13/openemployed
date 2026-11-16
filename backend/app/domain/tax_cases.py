"""Steuerfälle (siehe docs/konzept/01-steuern-und-buchhaltung.md §3 und 08 §3.2).

Jeder Rechnungsposition und jedem Beleg ist genau ein Steuerfall zugeordnet. Er bestimmt
Steuersatz, E-Rechnungs-Kategorie (EN 16931 BT-151/BT-118), Pflichthinweis und UStVA-Kennzahlen.

Aktiv sind nur Fälle, die für die UG relevant sind: Kunden ausschließlich in Deutschland (B2B und B2C),
Einkäufe auch bei EU-/Drittlandsanbietern (Reverse Charge).

⚠️ Kennzahlen jährlich mit dem amtlichen UStVA-Vordruck abgleichen; Steuersätze werden in Phase 1
in eine versionierte Tabelle mit Gültigkeitszeitraum überführt.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Direction(StrEnum):
    OUTGOING = "out"  # wir verkaufen
    INCOMING = "in"   # wir kaufen


class VatCategory(StrEnum):
    """EN 16931 / UNTDID 5305 Steuerkategorien."""

    STANDARD = "S"
    ZERO = "Z"
    EXEMPT = "E"
    REVERSE_CHARGE = "AE"
    INTRA_COMMUNITY = "K"
    OUTSIDE_SCOPE = "O"


@dataclass(frozen=True, slots=True)
class TaxCase:
    code: str
    direction: Direction
    label: str
    rate: Decimal                      # Prozent, z. B. Decimal("19")
    category: VatCategory
    active: bool
    reverse_charge: bool = False       # wir schulden die USt (Eingang) bzw. Kunde schuldet (Ausgang)
    vatex: str | None = None           # Befreiungsgrund-Code (BT-121)
    invoice_note: str | None = None    # Pflichthinweis auf der Rechnung (BT-120)
    ustva_base: str | None = None      # Kennzahl Bemessungsgrundlage
    ustva_tax: str | None = None       # Kennzahl Steuer (falls separat)
    ustva_input_tax: str | None = None  # Kennzahl Vorsteuer
    vat_deductible: bool = True        # Vorsteuer abziehbar (Eingang)


_D = Decimal

TAX_CASES: tuple[TaxCase, ...] = (
    # ───────── Ausgangsseite ─────────
    TaxCase("OUT_DE_19", Direction.OUTGOING, "Inland, Regelsteuersatz 19 %", _D("19"), VatCategory.STANDARD,
            active=True, ustva_base="81"),
    TaxCase("OUT_DE_7", Direction.OUTGOING, "Inland, ermäßigter Steuersatz 7 %", _D("7"), VatCategory.STANDARD,
            active=True, ustva_base="86"),
    TaxCase("OUT_EU_B2B_SERVICE", Direction.OUTGOING, "Sonstige Leistung an Unternehmer im EU-Ausland",
            _D("0"), VatCategory.REVERSE_CHARGE, active=True, reverse_charge=True, vatex="VATEX-EU-AE",
            invoice_note="Steuerschuldnerschaft des Leistungsempfängers", ustva_base="21"),
    TaxCase("OUT_EU_B2B_GOODS", Direction.OUTGOING, "Innergemeinschaftliche Lieferung", _D("0"),
            VatCategory.INTRA_COMMUNITY, active=True, vatex="VATEX-EU-IC",
            invoice_note="Steuerfreie innergemeinschaftliche Lieferung", ustva_base="41"),
    TaxCase("OUT_DRITTLAND_SERVICE", Direction.OUTGOING, "Sonstige Leistung an Unternehmer im Drittland", _D("0"),
            VatCategory.OUTSIDE_SCOPE, active=True, vatex="VATEX-EU-O",
            invoice_note="Nicht im Inland steuerbare Leistung", ustva_base="45"),
    # ───────── Eingangsseite ─────────
    TaxCase("IN_DE_19", Direction.INCOMING, "Inland 19 % Vorsteuer", _D("19"), VatCategory.STANDARD,
            active=True, ustva_input_tax="66"),
    TaxCase("IN_DE_7", Direction.INCOMING, "Inland 7 % Vorsteuer", _D("7"), VatCategory.STANDARD,
            active=True, ustva_input_tax="66"),
    TaxCase("IN_13B_EU_SERVICE", Direction.INCOMING, "Leistung eines EU-Unternehmers (§ 13b Abs. 1 UStG)",
            _D("19"), VatCategory.REVERSE_CHARGE, active=True, reverse_charge=True,
            ustva_base="46", ustva_tax="47", ustva_input_tax="67"),
    TaxCase("IN_13B_DRITTLAND_SERVICE", Direction.INCOMING,
            "Leistung eines Unternehmers aus dem Drittland (§ 13b Abs. 2 Nr. 1 UStG)", _D("19"),
            VatCategory.REVERSE_CHARGE, active=True, reverse_charge=True,
            ustva_base="84", ustva_tax="85", ustva_input_tax="67"),
    TaxCase("IN_IG_ERWERB", Direction.INCOMING, "Innergemeinschaftlicher Erwerb 19 %", _D("19"),
            VatCategory.INTRA_COMMUNITY, active=True, reverse_charge=True,
            ustva_base="89", ustva_input_tax="61"),
    TaxCase("IN_EINFUHR", Direction.INCOMING, "Einfuhrumsatzsteuer", _D("19"), VatCategory.STANDARD,
            active=True, ustva_input_tax="62"),
    TaxCase("IN_OHNE_VST", Direction.INCOMING, "Ohne Vorsteuer (z. B. Kleinunternehmer, ausländische USt)",
            _D("0"), VatCategory.OUTSIDE_SCOPE, active=True, vat_deductible=False),
    TaxCase("IN_BEWIRTUNG", Direction.INCOMING, "Bewirtung (Vorsteuer voll, Aufwand 70 % abziehbar)", _D("19"),
            VatCategory.STANDARD, active=True, ustva_input_tax="66"),
    TaxCase("IN_NICHT_ABZIEHBAR", Direction.INCOMING, "Nicht abziehbare Betriebsausgabe (z. B. Geschenke > 50 €)",
            _D("19"), VatCategory.STANDARD, active=True, vat_deductible=False),
)

_BY_CODE: dict[str, TaxCase] = {c.code: c for c in TAX_CASES}
if len(_BY_CODE) != len(TAX_CASES):  # pragma: no cover – Schutz gegen doppelte Codes
    raise RuntimeError("doppelter Steuerfall-Code")


class UnknownTaxCaseError(ValueError):
    pass


def get_tax_case(code: str) -> TaxCase:
    try:
        return _BY_CODE[code]
    except KeyError:
        raise UnknownTaxCaseError(f"Unbekannter Steuerfall: {code}") from None


def active_cases(direction: Direction | None = None) -> list[TaxCase]:
    return [c for c in TAX_CASES if c.active and (direction is None or c.direction == direction)]
