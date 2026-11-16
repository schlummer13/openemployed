"""Lohnarten und Sonderfälle der Entgeltabrechnung.

- Dienstwagen (§ 8 Abs. 2 EStG): 1 % des auf volle 100 € abgerundeten Bruttolistenpreises je Monat, zzgl. 0,03 % je Entfernungs-km
  für Fahrten Wohnung–Arbeit; Elektro 0,25 % (Listenpreis bis 100.000 €, sonst 0,5 %), Plug-in-Hybrid 0,5 %; Zuzahlungen mindern.
- Pfändung (§ 850c ZPO): unpfändbarer Grundbetrag zzgl. Erhöhung je Unterhaltsberechtigtem; vom Mehrbetrag sind 70 % (ohne
  Unterhaltspflicht), 50/40/30/20/10 % (1/2/3/4/5+ Unterhaltsberechtigte) pfändbar; oberhalb des Höchstbetrags alles. Berechnung
  auf das auf volle 10 € abgerundete Nettoeinkommen (Pfändungstabelle). Werte: Pfändungsfreigrenzenbekanntmachung, gültig ab 1.7.
- Entgeltfortzahlung (§ 3 EFZG): bis 6 Wochen je Arbeitsunfähigkeit; danach Krankengeld der Krankenkasse – kein Entgelt, SV-Tage
  entfallen (Unterbrechungsmeldung, wenn länger als ein Kalendermonat).
- Unbezahlter Urlaub, Elternzeit, Mutterschutz: Entgelt anteilig nach Kalendertagen (30-Tage-Methode bei Monatsgehalt);
  Mutterschutz: Zuschuss zum Mutterschaftsgeld = Nettoentgelt/Tag − 13 € (§ 20 MuSchG), steuer- und SV-frei.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

D = Decimal
CENT = D("0.01")
ZERO = D(0)
G_BASE, G_FIRST, G_MORE, G_CAP = D("1555.00"), D("585.23"), D("326.04"), D("4766.99")


def company_car_benefit(list_price: D, distance_km: D, kind: str = "combustion", copayment: D = ZERO) -> D:
    lp = (list_price / 100).to_integral_value(rounding=ROUND_DOWN) * 100
    if kind == "electric":
        factor = D("0.25") if list_price <= 100_000 else D("0.5")
    elif kind == "hybrid":
        factor = D("0.5")
    else:
        factor = D(1)
    private = lp * D("0.01") * factor
    commute = lp * D("0.0003") * distance_km * factor
    return max(D(0), (private + commute - copayment).quantize(CENT, rounding=ROUND_DOWN))


@dataclass(frozen=True, slots=True)
class GarnishmentParams:
    """Monatswerte nach § 850c ZPO (Pfändungsfreigrenzenbekanntmachung 2025, gültig 1.7.2025–30.6.2026) ⚠️ jährlich prüfen."""

    base: D = G_BASE
    first_dependent: D = G_FIRST
    further_dependent: D = G_MORE
    cap: D = G_CAP


GARNISH_SHARE = {0: D("0.7"), 1: D("0.5"), 2: D("0.4"), 3: D("0.3"), 4: D("0.2")}


def garnishable(net: D, dependents: int, p: GarnishmentParams = GarnishmentParams()) -> D:
    """Pfändbarer Betrag des Nettoeinkommens (§ 850c ZPO)."""
    deps = max(0, dependents)
    free = p.base + (p.first_dependent if deps >= 1 else D(0)) + p.further_dependent * min(max(0, deps - 1), 4)
    rounded = (net / 10).to_integral_value(rounding=ROUND_DOWN) * 10
    if rounded <= free:
        return D(0)
    share = GARNISH_SHARE.get(deps, D("0.1"))
    within = min(rounded, p.cap) - free
    above = max(D(0), net - p.cap)
    return (within * share + above).quantize(CENT, rounding=ROUND_DOWN)


@dataclass(frozen=True, slots=True)
class AbsenceEffect:
    unpaid_days: int          # Kalendertage ohne Entgeltanspruch (unbezahlt, Elternzeit, Mutterschutz, Krankengeld)
    sv_lost_days: int         # SV-Tage, die entfallen (Krankengeld, Elterngeld, Mutterschaftsgeld)
    maternity_days: int
    notes: tuple[str, ...]
    reports: tuple[tuple[str, dt.date, dt.date | None], ...]  # (DEÜV-Grund, von, bis)


def _days(a: dt.date, b: dt.date) -> int:
    return (b - a).days + 1


def absence_effect(absences: list[dict[str, object]], first: dt.date, last: dt.date) -> AbsenceEffect:
    """Wirkung genehmigter Abwesenheiten auf das Entgelt eines Monats."""
    unpaid = sv_lost = maternity = 0
    notes: list[str] = []
    reports: list[tuple[str, dt.date, dt.date | None]] = []
    # Krankheit: zusammenhängende Zeiträume (Lücken bis 1 Tag zusammengefasst) – ab Tag 43 Krankengeld
    sick = sorted((a for a in absences if a["kind"] == "sick"), key=lambda a: a["start_date"])  # type: ignore[arg-type, return-value]
    episodes: list[list[dt.date]] = []
    for a in sick:
        s, e = a["start_date"], a["end_date"]
        if not (isinstance(s, dt.date) and isinstance(e, dt.date)):
            continue
        if episodes and (s - episodes[-1][1]).days <= 2:
            episodes[-1][1] = max(episodes[-1][1], e)
        else:
            episodes.append([s, e])
    for s, e in episodes:
        efz_end = s + dt.timedelta(days=41)
        if e > efz_end:
            lo, hi = max(first, efz_end + dt.timedelta(days=1)), min(last, e)
            if lo <= hi:
                n = _days(lo, hi)
                unpaid += n
                sv_lost += n
                notes.append(f"Krankengeld ab {efz_end + dt.timedelta(days=1):%d.%m.%Y} (Entgeltfortzahlung 6 Wochen beendet): "
                             f"{n} Kalendertage ohne Entgelt")
                if (e - efz_end).days > 30:
                    reports.append(("51", efz_end + dt.timedelta(days=1), e))
    for a in absences:
        kind = a["kind"]
        if kind not in ("unpaid", "parental", "maternity"):
            continue
        s, e = a["start_date"], a["end_date"]
        if not (isinstance(s, dt.date) and isinstance(e, dt.date)):
            continue
        lo, hi = max(first, s), min(last, e)
        if lo > hi:
            continue
        n = _days(lo, hi)
        unpaid += n
        label = {"unpaid": "Unbezahlter Urlaub", "parental": "Elternzeit", "maternity": "Mutterschutz"}[str(kind)]
        notes.append(f"{label}: {n} Kalendertage ohne Entgelt")
        if kind in ("parental", "maternity"):
            sv_lost += n
            if kind == "maternity":
                maternity += n
            reports.append(("52" if kind == "parental" else "51", s, e))
        elif _days(s, e) > 31:
            reports.append(("34", s, e))
    month_days = _days(first, last)
    return AbsenceEffect(min(unpaid, month_days), min(sv_lost, month_days), maternity, tuple(notes), tuple(reports))
