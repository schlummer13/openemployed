"""Gehaltsabrechnung mit Einzelpositionen (Domäne)."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.domain.payroll import PERSON_GROUPS, TaxProfile, contribution_group_key, midijob_bases, payslip, salary


def test_regular_matches_planning_and_balances() -> None:
    prof = TaxProfile(tax_class=1)
    p = payslip(D("4000"), "regular", profile=prof)
    s = salary(D("4000"), profile=prof)
    assert (p.sv_ee, p.employer_cost) == (s.sv_employee_month, s.employer_cost_month)
    # Lohnsteuer jetzt nach amtlichem PAP; die Planungsrechnung bleibt eine Näherung (±5 %)
    assert abs(p.lst - s.lst_month) / p.lst < D("0.05")
    assert p.gross - p.taxes - p.sv_ee == p.net == p.payout
    assert p.pv_ee > p.pv_er, "Kinderlosenzuschlag nur AN"


def test_minijob_and_limits() -> None:
    p = payslip(D("556"), "minijob", profile=TaxProfile())
    assert (p.rv_er, p.kv_er, p.flat_tax, p.rv_ee, p.lst) == (D("83.40"), D("72.28"), D("11.12"), D("20.02"), 0)
    assert payslip(D("556"), "minijob", profile=TaxProfile(), rv_exempt=True).rv_ee == 0
    with pytest.raises(ValueError, match="Geringfügigkeitsgrenze"):
        payslip(D("700"), "minijob", profile=TaxProfile())
    assert payslip(D("1500"), "regular", profile=TaxProfile()).notes, "Midijob-Hinweis"


def test_midijob_transition_zone() -> None:
    """§ 20a SGB IV: F = 28/42,3 = 0,6619; BE und BE_AN nach Formel, Randwerte stetig."""
    assert midijob_bases(D("603")) is None and midijob_bases(D("2000.01")) is None
    m = midijob_bases(D("1000"))
    assert m is not None and m.factor == D("0.6619")
    # BE = 0,6619 × 603 + (2000/1397 − 603/1397 × 0,6619) × 397 = 854,06; BE_AN = 2000/1397 × 397 = 568,36
    assert (m.total_base, m.employee_base) == (D("854.06"), D("568.36"))
    top = midijob_bases(D("2000"))
    assert top is not None and top.total_base == top.employee_base == D("2000.00"), "an der Obergrenze voller Beitrag"
    prof = TaxProfile(tax_class=1, children_under_25=1)
    p = payslip(D("1000"), "regular", profile=prof)
    assert p.rv_ee == D("52.86") and p.rv_ee + p.rv_er == D("158.86"), "RV: AN aus BE_AN, Summe aus BE"
    assert p.av_ee == D("7.39") and p.kv_ee == D("49.73")
    full = payslip(D("2100"), "regular", profile=prof)
    assert full.rv_ee == full.rv_er == D("195.30"), "oberhalb: normale Halbteilung"
    assert p.levies == D("3.70"), "Umlagen vom tatsächlichen Entgelt"


def test_group_keys() -> None:
    assert contribution_group_key("regular") == "1111" and contribution_group_key("regular", health_insurance="pkv") == "0110"
    assert contribution_group_key("minijob") == "6100" and contribution_group_key("minijob", rv_exempt=True) == "6500"
    assert contribution_group_key("werkstudent") == "0100" and contribution_group_key("shareholder") == "0000"
    assert PERSON_GROUPS["minijob"][0] == "109" and PERSON_GROUPS["werkstudent"][0] == "106"


def test_werkstudent_only_pension_and_pkv_subsidy() -> None:
    w = payslip(D("1500"), "werkstudent", profile=TaxProfile())
    assert w.rv_ee == D("139.50") and w.kv_ee == w.av_ee == w.pv_ee == 0
    pkv = payslip(D("7000"), "regular", profile=TaxProfile(health_insurance="pkv", pkv_premium=D("800")))
    assert pkv.kv_ee == 0 and pkv.pkv_subsidy == D("400.00") and pkv.payout == pkv.net + D("400.00")
    free = payslip(D("5000"), "regular", profile=TaxProfile(), sv_free=True)
    assert free.sv_ee == free.sv_er == free.levies == 0


def test_one_time_benefits_and_deductions() -> None:
    prof = TaxProfile(tax_class=1, children_under_25=1)
    base = payslip(D("3000"), "regular", profile=prof)
    xmas = payslip(D("3000"), "regular", profile=prof, one_time=D("3000"), annual_regular=D("36000"))
    assert xmas.gross == D("6000") and xmas.one_time == D("3000")
    assert xmas.lst > base.lst * 2, "sonstiger Bezug nach Jahreslohnverfahren deutlich höher belastet"
    assert xmas.sv_base_rv == D("6000.00") and xmas.rv_ee == D("558.00")
    car = payslip(D("3000"), "regular", profile=prof, benefits=D("450"), tax_free=D("60"), deductions=D("200"))
    assert car.gross == D("3450") and car.lst > base.lst
    assert car.payout == car.net - D("450") + D("60") - D("200"), "Sachbezug vom Netto ab, Erstattung dazu, Vorschuss ab"
    capped = payslip(D("8000"), "regular", profile=prof, one_time=D("5000"), sv_room=(D("1000"), D("0")))
    assert capped.sv_base_rv == D("9000") and capped.sv_base_kv == D("5812.50"), "Einmalzahlung nur bis zur anteiligen Jahres-BBG"
