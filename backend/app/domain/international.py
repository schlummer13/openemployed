"""Auslandsgeschäft: EU-Länder, Prüfung der Steuerfälle gegen den Kunden, Fremdwährungsumrechnung."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

EU = frozenset({"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV",
                "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"})
# USt-IdNr.-Präfix weicht bei Griechenland ab (EL)
VAT_PREFIX = {"GR": "EL"}
CURRENCIES = ("EUR", "USD", "GBP", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "JPY", "CAD", "AUD")


def cents(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_eur(amount: Decimal | str, rate: Decimal | str | None) -> Decimal:
    """EZB-Kurs = Fremdwährung je 1 EUR (z. B. 1,0850 USD) → Betrag / Kurs."""
    a = Decimal(str(amount))
    return cents(a / Decimal(str(rate))) if rate else cents(a)


def tax_case_problems(buyer: dict[str, Any], tax_cases: set[str]) -> list[str]:
    country = (buyer.get("country_code") or "DE").upper()
    vat = (buyer.get("vat_id") or "").replace(" ", "").upper()
    out: list[str] = []
    domestic = {c for c in tax_cases if c.startswith("OUT_DE")}
    eu = {c for c in tax_cases if c.startswith("OUT_EU")}
    third = {c for c in tax_cases if c == "OUT_DRITTLAND_SERVICE"}
    if eu:
        if country == "DE" or country not in EU:
            out.append("EU-Steuerfälle nur für Kunden in einem anderen EU-Staat")
        if buyer.get("type") != "b2b":
            out.append("Steuerfreie EU-Umsätze nur an Unternehmer (B2B)")
        if not vat or not vat.startswith(VAT_PREFIX.get(country, country)):
            out.append(f"USt-IdNr. des Kunden aus {country} fehlt (Pflicht für Reverse Charge/ig. Lieferung, § 14a UStG)")
    if third and (country in EU):
        out.append("Drittland-Steuerfall nur für Kunden außerhalb der EU")
    if third and buyer.get("type") != "b2b":
        out.append("Nicht steuerbare Leistungen im Drittland hier nur an Unternehmer (B2B)")
    if domestic and country != "DE" and buyer.get("type") == "b2b" and country in EU:
        out.append("Firmenkunde im EU-Ausland: Steuerfall „Sonstige Leistung an Unternehmer im EU-Ausland“ bzw. "
                   "„Innergemeinschaftliche Lieferung“ wählen (Ausnahmen mit dem Steuerberater klären)")
    return out


def vat_id_problem(vat: str, country: str) -> str | None:
    v = vat.replace(" ", "").upper()
    prefix = VAT_PREFIX.get(country, country)
    if country == "DE":
        return None if len(v) == 11 and v.startswith("DE") and v[2:].isdigit() else "USt-IdNr. im Format DE123456789"
    if country in EU and (not v.startswith(prefix) or not 4 <= len(v) <= 15 or not v[2:].isalnum()):
        return f"USt-IdNr. muss mit {prefix} beginnen"
    return None
