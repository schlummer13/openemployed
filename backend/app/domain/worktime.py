"""Arbeitszeit und Urlaub: gesetzliche Pausen, Feiertage je Bundesland, Arbeitstage und Urlaubsanspruch (BUrlG).

- Pausen § 4 ArbZG (unabhängig von Minijob, Teilzeit, Vollzeit): mehr als 6 Std. → 30 Min., mehr als 9 Std. → 45 Min.
  Minderjährige § 11 JArbSchG: mehr als 4,5 Std. → 30 Min., mehr als 6 Std. → 60 Min. Abgezogen wird nur so viel, dass die
  Arbeitszeit die Schwelle nicht unterschreitet (6 Std. 10 Min. Anwesenheit → 6 Std. Arbeitszeit).
- Urlaub: Anspruch laut Vertrag (Gesamtzahl je Jahr, bezogen auf die eigenen Arbeitstage). Eintrittsjahr: Eintritt bis 30.6.
  → voller Anspruch (Wartezeit § 4 wird im Jahr erfüllt), sonst 1/12 je vollem Beschäftigungsmonat (§ 5 Abs. 1a).
  Austrittsjahr: Austritt bis 30.6. → 1/12 je vollem Monat (§ 5 Abs. 1c); danach gesetzlicher Mindesturlaub voll, darüber
  hinausgehender Vertragsurlaub anteilig. Bruchteile ab einem halben Tag werden aufgerundet (§ 5 Abs. 2), kleinere auf halbe
  Tage abgerundet. Resturlaub verfällt zum 31.3. des Folgejahres.
"""

from __future__ import annotations

import calendar
import datetime as dt
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

D = Decimal


def required_break(presence_min: int, *, minor: bool = False) -> int:
    if minor:
        return min(30, max(0, presence_min - 270)) + min(30, max(0, presence_min - 390))
    return min(30, max(0, presence_min - 360)) + min(15, max(0, presence_min - 570))


def is_minor(birth: dt.date | None, on: dt.date) -> bool:
    if birth is None:
        return False
    age = on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))
    return age < 18


# ───────── Feiertage ─────────

def easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d_, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d_ - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 19 * m) // 433
    month = (h + m - 7 * n + 90) // 25
    return dt.date(year, month, (h + m - 7 * n + 33 * month + 19) % 32)


STATE_NAMES = {
    "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin", "BB": "Brandenburg", "HB": "Bremen", "HH": "Hamburg", "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern", "NI": "Niedersachsen", "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz", "SL": "Saarland",
    "SN": "Sachsen", "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein", "TH": "Thüringen",
}


def holidays(year: int, state: str) -> dict[dt.date, str]:
    """Gesetzliche Feiertage eines Bundeslands – für jedes Jahr vorausberechnet (Ostern nach Gauß), inkl. Stichtagen neuer Feiertage."""
    e = easter(year)
    out = {dt.date(year, 1, 1): "Neujahr", e - dt.timedelta(days=2): "Karfreitag", e + dt.timedelta(days=1): "Ostermontag",
           dt.date(year, 5, 1): "Tag der Arbeit", e + dt.timedelta(days=39): "Christi Himmelfahrt",
           e + dt.timedelta(days=50): "Pfingstmontag", dt.date(year, 10, 3): "Tag der Deutschen Einheit",
           dt.date(year, 12, 25): "1. Weihnachtstag", dt.date(year, 12, 26): "2. Weihnachtstag"}
    if state in ("BW", "BY", "ST"):
        out[dt.date(year, 1, 6)] = "Heilige Drei Könige"
    if (state == "BE" and year >= 2019) or (state == "MV" and year >= 2023):
        out[dt.date(year, 3, 8)] = "Internationaler Frauentag"
    if state == "BE" and year in (2020, 2025):
        out[dt.date(year, 5, 8)] = "Tag der Befreiung (einmalig)"
    if state in ("BW", "BY", "HE", "NW", "RP", "SL"):
        out[e + dt.timedelta(days=60)] = "Fronleichnam"
    if state == "SL":
        out[dt.date(year, 8, 15)] = "Mariä Himmelfahrt"
    if state == "TH" and year >= 2019:
        out[dt.date(year, 9, 20)] = "Weltkindertag"
    if (state in ("BB", "MV", "SN", "ST", "TH") or (state in ("HB", "HH", "NI", "SH") and year >= 2018)
            or year == 2017):
        out[dt.date(year, 10, 31)] = "Reformationstag" + (" (500 Jahre, bundesweit)" if year == 2017 else "")
    if state in ("BW", "BY", "NW", "RP", "SL"):
        out[dt.date(year, 11, 1)] = "Allerheiligen"
    if state == "SN":
        out[repentance_day(year)] = "Buß- und Bettag"
    return out


def repentance_day(year: int) -> dt.date:
    nov23 = dt.date(year, 11, 23)
    return nov23 - dt.timedelta(days=(nov23.weekday() - 2) % 7 or 7)


def regional_holidays(year: int, state: str) -> dict[dt.date, str]:
    """Feiertage, die nur in Teilen eines Bundeslands gelten – nicht automatisch arbeitsfrei, als betrieblicher Tag übernehmbar."""
    e = easter(year)
    out: dict[dt.date, str] = {}
    if state == "BY":
        out[dt.date(year, 8, 15)] = "Mariä Himmelfahrt (überwiegend katholische Gemeinden)"
        out[dt.date(year, 8, 8)] = "Augsburger Friedensfest (nur Stadt Augsburg)"
    if state == "SN":
        out[e + dt.timedelta(days=60)] = "Fronleichnam (einige sorbische Gemeinden)"
    if state == "TH":
        out[e + dt.timedelta(days=60)] = "Fronleichnam (Eichsfeld und einige Gemeinden)"
    return out


# Brauchtums- und Brückentage, die viele Firmen ganz oder halb freigeben (keine gesetzlichen Feiertage)
CUSTOMARY: tuple[dict[str, object], ...] = (
    {"name": "Heiligabend", "rule": "fixed", "month": 12, "day": 24, "half_day": True},
    {"name": "Silvester", "rule": "fixed", "month": 12, "day": 31, "half_day": True},
    {"name": "Rosenmontag", "rule": "easter", "easter_offset": -48, "half_day": False, "states": ("NW", "RP", "HE", "BW", "SL")},
    {"name": "Weiberfastnacht", "rule": "easter", "easter_offset": -52, "half_day": True, "states": ("NW", "RP")},
    {"name": "Faschingsdienstag", "rule": "easter", "easter_offset": -47, "half_day": True, "states": ("BY", "BW")},
)


def rule_date(year: int, rule: str, *, month: int | None = None, day: int | None = None,
              easter_offset: int | None = None) -> dt.date | None:
    if rule == "easter" and easter_offset is not None:
        return easter(year) + dt.timedelta(days=easter_offset)
    if month and day:
        try:
            return dt.date(year, month, day)
        except ValueError:  # 29.2. in Nicht-Schaltjahren
            return None
    return None


def bridge_days(year: int, off: set[dt.date], work_days: tuple[int, ...] = (0, 1, 2, 3, 4)) -> list[dict[str, object]]:
    """Brückentage: einzelne Arbeitstage zwischen Feiertag und Wochenende (bzw. zwei freien Tagen) – 1 Urlaubstag, mehrere Tage frei."""
    def free(d: dt.date) -> bool:
        return d.weekday() not in work_days or d in off

    out = []
    d, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    while d <= end:
        if not free(d) and free(d - dt.timedelta(days=1)) and free(d + dt.timedelta(days=1)):
            lo, hi = d - dt.timedelta(days=1), d + dt.timedelta(days=1)
            while free(lo - dt.timedelta(days=1)):
                lo -= dt.timedelta(days=1)
            while free(hi + dt.timedelta(days=1)):
                hi += dt.timedelta(days=1)
            if any(x in off for x in (d - dt.timedelta(days=1), d + dt.timedelta(days=1))):
                out.append({"date": d, "free_from": lo, "free_to": hi, "days_off": (hi - lo).days + 1})
        d += dt.timedelta(days=1)
    return out


def workdays(start: dt.date, end: dt.date, *, state: str, work_days: tuple[int, ...] = (0, 1, 2, 3, 4),
             half_start: bool = False, half_end: bool = False, company: dict[dt.date, D] | None = None) -> D:
    """Urlaubstage zwischen zwei Daten: nur eigene Arbeitstage, ohne gesetzliche Feiertage; halbe Tage am Anfang/Ende.

    ``company``: betriebliche freie Tage (1 = ganzer Tag frei, 0.5 = halber Tag frei, z. B. Heiligabend).
    """
    if end < start:
        return D(0)
    hol: dict[dt.date, str] = {}
    for y in range(start.year, end.year + 1):
        hol |= holidays(y, state)
    company = company or {}
    days = D(0)
    d = start
    while d <= end:
        if d.weekday() in work_days and d not in hol:
            day = D("0.5") if (d == start and half_start) or (d == end and half_end) else D(1)
            days += max(D(0), day - company.get(d, D(0)))
        d += dt.timedelta(days=1)
    return days


# ───────── Urlaubsanspruch ─────────

def _round(v: D) -> D:
    frac = v - v.to_integral_value(rounding=ROUND_FLOOR)
    if frac >= D("0.5"):
        return v.to_integral_value(rounding=ROUND_CEILING)
    return (v * 2).to_integral_value(rounding=ROUND_FLOOR) / 2


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def full_months(start: dt.date, end: dt.date) -> int:
    """Volle Beschäftigungsmonate im Zeitraum (z. B. 01.07.–31.12. = 6, 15.03.–31.12. = 9)."""
    n = 0
    while _add_months(start, n + 1) - dt.timedelta(days=1) <= end:
        n += 1
    return n


def entitlement(year: int, annual: D, *, entry: dt.date, exit_: dt.date | None, work_days_per_week: int = 5) -> dict[str, object]:
    """Urlaubsanspruch eines Kalenderjahres mit Erläuterung."""
    annual = D(annual)
    statutory = D(4) * work_days_per_week  # 24 Werktage bei 6-Tage-Woche = 4 Wochen
    year_start, year_end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    start = max(entry, year_start)
    end = min(exit_, year_end) if exit_ else year_end
    if end < start:
        return {"days": D(0), "rule": "nicht beschäftigt", "months": 0}
    months = full_months(start, end)
    waiting_until = entry + dt.timedelta(days=183)
    if entry > year_start and entry.year == year and (entry.month, entry.day) > (6, 30):
        days, rule = _round(annual * months / 12), f"Eintritt nach dem 30.6.: {months}/12 des Jahresurlaubs (§ 5 Abs. 1a BUrlG)"
    elif exit_ and exit_.year == year and (exit_.month, exit_.day) <= (6, 30):
        days, rule = _round(annual * months / 12), f"Austritt bis 30.6.: {months}/12 des Jahresurlaubs (§ 5 Abs. 1c BUrlG)"
    elif exit_ and exit_.year == year and waiting_until <= exit_:
        extra = max(D(0), annual - statutory)
        days = min(annual, statutory + _round(extra * months / 12))
        rule = f"Austritt in der zweiten Jahreshälfte: gesetzlicher Mindesturlaub voll ({statutory}), Vertragsurlaub {months}/12"
    elif entry.year == year and entry > year_start:
        days, rule = annual, "Eintritt bis 30.6.: voller Jahresurlaub nach Ablauf der Wartezeit (§ 4 BUrlG)"
    else:
        days, rule = annual, "voller Jahresurlaub"
    return {"days": days, "rule": rule, "months": months, "waiting_until": waiting_until if entry.year >= year - 1 else None,
            "statutory_minimum": statutory}
