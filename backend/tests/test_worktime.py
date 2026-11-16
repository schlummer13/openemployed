"""Pausen (ArbZG/JArbSchG), Feiertage je Land, Urlaubstage und Urlaubsanspruch (BUrlG)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from app.domain.worktime import (
    bridge_days,
    easter,
    entitlement,
    full_months,
    holidays,
    is_minor,
    regional_holidays,
    required_break,
    rule_date,
    workdays,
)


def test_breaks() -> None:
    assert required_break(360) == 0 and required_break(370) == 10 and required_break(390) == 30
    assert required_break(570) == 30 and required_break(580) == 40 and required_break(600) == 45
    assert required_break(300, minor=True) == 30 and required_break(450, minor=True) == 60
    assert is_minor(dt.date(2009, 12, 1), dt.date(2026, 11, 15)) and not is_minor(dt.date(2008, 11, 15), dt.date(2026, 11, 15))


def test_holidays_and_workdays() -> None:
    nw, be = holidays(2026, "NW"), holidays(2026, "BE")
    assert dt.date(2026, 6, 4) in nw and dt.date(2026, 6, 4) not in be, "Fronleichnam nur in NRW u. a."
    assert dt.date(2026, 3, 8) in be and dt.date(2026, 4, 3) in nw, "Frauentag Berlin, Karfreitag überall"
    assert dt.date(2026, 11, 18) in holidays(2026, "SN"), "Buß- und Bettag 2026"
    # Osterwoche 30.3.–10.4.2026 in NRW: 10 Werktage minus Karfreitag und Ostermontag = 8
    assert workdays(dt.date(2026, 3, 30), dt.date(2026, 4, 10), state="NW") == D(8)
    assert workdays(dt.date(2026, 3, 30), dt.date(2026, 4, 10), state="NW", work_days=(0, 1, 2)) == D(5), "Teilzeit Mo–Mi"
    assert workdays(dt.date(2026, 7, 6), dt.date(2026, 7, 10), state="NW", half_end=True) == D("4.5")


def test_holidays_ahead_and_history() -> None:
    # Ostersonntag für weit entfernte Jahre (bekannte Termine)
    assert easter(2027) == dt.date(2027, 3, 28) and easter(2035) == dt.date(2035, 3, 25) and easter(2050) == dt.date(2050, 4, 10)
    assert easter(2038) == dt.date(2038, 4, 25) and easter(2285) == dt.date(2285, 3, 22), "spätester und frühester Ostertermin"
    assert len(holidays(2040, "NW")) == 11 and len(holidays(2040, "BY")) == 12 and len(holidays(2040, "BE")) == 10
    # Stichtage: Reformationstag Norddeutschland seit 2018, 2017 bundesweit; Frauentag MV seit 2023; Befreiungstag Berlin einmalig
    assert dt.date(2016, 10, 31) not in holidays(2016, "HH") and dt.date(2018, 10, 31) in holidays(2018, "HH")
    assert dt.date(2017, 10, 31) in holidays(2017, "NW")
    assert dt.date(2022, 3, 8) not in holidays(2022, "MV") and dt.date(2023, 3, 8) in holidays(2023, "MV")
    assert dt.date(2025, 5, 8) in holidays(2025, "BE") and dt.date(2026, 5, 8) not in holidays(2026, "BE")
    assert dt.date(2026, 8, 15) in regional_holidays(2026, "BY") and dt.date(2026, 8, 15) not in holidays(2026, "BY")
    assert rule_date(2027, "easter", easter_offset=-48) == dt.date(2027, 2, 8), "Rosenmontag 2027"
    assert rule_date(2027, "fixed", month=2, day=29) is None


def test_bridges_and_company_days() -> None:
    off = set(holidays(2027, "NW"))
    assert [b["date"] for b in bridge_days(2027, off)] == [dt.date(2027, 5, 7), dt.date(2027, 5, 28)]
    assert bridge_days(2027, off)[0]["days_off"] == 4, "Do Himmelfahrt + Brückentag Fr + Wochenende"
    # 20.–31.12.2027: 10 Werktage (Weihnachten fällt aufs Wochenende); Heiligabend und Silvester halb frei → 9
    company = {dt.date(2027, 12, 24): D("0.5"), dt.date(2027, 12, 31): D("0.5")}
    assert workdays(dt.date(2027, 12, 20), dt.date(2027, 12, 31), state="NW") == D(10)
    assert workdays(dt.date(2027, 12, 20), dt.date(2027, 12, 31), state="NW", company=company) == D(9)
    assert workdays(dt.date(2027, 2, 8), dt.date(2027, 2, 8), state="NW", company={dt.date(2027, 2, 8): D(1)}) == D(0), "Rosenmontag frei"


def test_entitlement() -> None:
    assert full_months(dt.date(2026, 7, 1), dt.date(2026, 12, 31)) == 6 and full_months(dt.date(2026, 3, 15), dt.date(2026, 12, 31)) == 9
    assert entitlement(2026, D(30), entry=dt.date(2024, 1, 1), exit_=None)["days"] == D(30)
    assert entitlement(2026, D(30), entry=dt.date(2026, 3, 1), exit_=None)["days"] == D(30), "Eintritt bis 30.6. → voll"
    assert entitlement(2026, D(30), entry=dt.date(2026, 9, 1), exit_=None)["days"] == D(10), "4/12 von 30"
    assert entitlement(2026, D(28), entry=dt.date(2026, 8, 1), exit_=None)["days"] == D(12), "5/12×28 = 11,67 → 12"
    assert entitlement(2026, D(26), entry=dt.date(2026, 10, 1), exit_=None)["days"] == D(7), "3/12×26 = 6,5 → 7 (§ 5 Abs. 2)"
    assert entitlement(2026, D(30), entry=dt.date(2020, 1, 1), exit_=dt.date(2026, 4, 30))["days"] == D(10), "Austritt bis 30.6.: 4/12"
    # Austritt 30.9.: gesetzlich 20 voll + Vertragsurlaub (10) × 9/12 = 7,5 → 8 (§ 5 Abs. 2)
    assert entitlement(2026, D(30), entry=dt.date(2020, 1, 1), exit_=dt.date(2026, 9, 30))["days"] == D(28)
    assert entitlement(2026, D(12), entry=dt.date(2026, 1, 1), exit_=None, work_days_per_week=2)["days"] == D(12)
    assert entitlement(2026, D(25), entry=dt.date(2026, 11, 20), exit_=None)["days"] == D(2), "1 voller Monat (20.11.–19.12.): 25/12 = 2,08 → 2"
