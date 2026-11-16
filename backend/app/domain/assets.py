"""Abschreibung (AfA) für das Anlagenverzeichnis – reine Rechenlogik ohne Datenbank.

- linear (§ 7 Abs. 1 EStG): monatsgenau; der Anschaffungsmonat zählt voll, im Abgangsjahr bis einschließlich Abgangsmonat.
- digital_full: Computerhardware/Software mit Nutzungsdauer 1 Jahr (BMF 26.02.2021 / 22.02.2022) – voll im Anschaffungsjahr.
- pool: Sammelposten (§ 6 Abs. 2a EStG, 250,01 € bis 1.000 € netto) – fünf Jahre je 20 %, unabhängig von Monat und Abgang.
- gwg: aktiviertes geringwertiges Wirtschaftsgut (§ 6 Abs. 2 EStG, bis 800 € netto) – voll im Anschaffungsjahr.

Kumulierte Rundung: Abschreibung bis Monat m = round(AK × m / ND); Jahresbetrag = Differenz der kumulierten Werte.
Dadurch ergibt die Summe aller Jahre exakt die Anschaffungskosten.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
POOL_YEARS = 5
POOL_MIN, POOL_MAX = Decimal("250.00"), Decimal("1000.00")
GWG_MAX = Decimal("800.00")

# Übliche Nutzungsdauern (AfA-Tabelle für allgemein verwendbare Anlagegüter, BMF 15.12.2000) – Vorschläge
USEFUL_LIFE = {
    "digital": 12,      # Computerhardware/Software (BMF 22.02.2022), alternativ 36 Monate
    "furniture": 156,   # Büromöbel 13 Jahre
    "vehicle": 72,      # Pkw 6 Jahre
    "machine": 84,      # Büromaschinen 7 Jahre
    "intangible": 36,   # erworbene Software/Lizenzen (ohne digitale Sonderregel) 3 Jahre
    "other": 60,
}


class AssetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Asset:
    method: str
    acquisition_date: dt.date
    cost: Decimal
    useful_life_months: int
    disposal_date: dt.date | None = None


@dataclass(frozen=True, slots=True)
class YearResult:
    depreciation: Decimal       # Abschreibung des Jahres
    disposal_book_value: Decimal  # Restbuchwert, der im Abgangsjahr ausgebucht wird (sonst 0)
    book_value_end: Decimal     # Buchwert 31.12. (nach Abgang 0)
    accumulated: Decimal        # kumulierte Abschreibung bis 31.12.


def _round(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _months_until(a: Asset, year: int) -> int:
    """Genutzte Monate von der Anschaffung bis zum Ende des Jahres (bzw. bis zum Abgangsmonat), inkl. Anschaffungsmonat."""
    end_year, end_month = year, 12
    if a.disposal_date and (a.disposal_date.year, a.disposal_date.month) < (year, 12):
        end_year, end_month = min((a.disposal_date.year, a.disposal_date.month), (year, 12))
    months = (end_year - a.acquisition_date.year) * 12 + end_month - a.acquisition_date.month + 1
    return max(0, months)


def accumulated_until(a: Asset, year: int) -> Decimal:
    """Kumulierte Abschreibung bis 31.12. des Jahres (ohne Ausbuchung des Restbuchwerts bei Abgang)."""
    if year < a.acquisition_date.year:
        return Decimal("0.00")
    if a.method in ("digital_full", "gwg"):
        return a.cost
    if a.method == "pool":
        k = min(POOL_YEARS, year - a.acquisition_date.year + 1)
        return min(a.cost, _round(a.cost * k / POOL_YEARS))
    m = min(a.useful_life_months, _months_until(a, year))
    return min(a.cost, _round(a.cost * m / a.useful_life_months))


def year_result(a: Asset, year: int) -> YearResult:
    if a.cost <= 0 or a.useful_life_months <= 0:
        raise AssetError("Anschaffungskosten und Nutzungsdauer müssen positiv sein")
    disposed_before = a.disposal_date is not None and a.disposal_date.year < year and a.method != "pool"
    if disposed_before or year < a.acquisition_date.year:
        acc = Decimal("0.00") if year < a.acquisition_date.year else accumulated_until(a, a.disposal_date.year)  # type: ignore[union-attr]
        return YearResult(Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), acc)
    acc_end = accumulated_until(a, year)
    acc_prev = accumulated_until(a, year - 1)
    dep = acc_end - acc_prev
    disposal = Decimal("0.00")
    book = a.cost - acc_end
    if a.disposal_date is not None and a.disposal_date.year == year and a.method != "pool":
        disposal, book = book, Decimal("0.00")
    return YearResult(dep, disposal, book, acc_end)


def suggest_method(cost: Decimal, asset_class: str) -> str:
    """Vorschlag nach Betrag: bis 800 € GWG, digitale Güter Sofortabschreibung, sonst linear."""
    if cost <= GWG_MAX:
        return "gwg"
    if asset_class == "digital":
        return "digital_full"
    return "linear"


def validate(method: str, cost: Decimal) -> None:
    if method == "pool" and not (POOL_MIN < cost <= POOL_MAX):
        raise AssetError("Sammelposten nur für Anschaffungskosten über 250 € bis 1.000 € netto (§ 6 Abs. 2a EStG)")
    if method == "gwg" and cost > GWG_MAX:
        raise AssetError("Sofortabschreibung als GWG nur bis 800 € netto (§ 6 Abs. 2 EStG)")
