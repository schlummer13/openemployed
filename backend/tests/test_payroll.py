from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.payroll import distribution, gross_for_employer_cost, income_tax, salary

D = Decimal


def test_income_tax_zones() -> None:
    assert income_tax(D("12348")) == 0            # Grundfreibetrag
    assert income_tax(D("12349")) == 0            # minimal
    assert income_tax(D("17799")) > 0
    assert income_tax(D("100000")) == D("30864")  # 0,42 × 100.000 − 11.135,63 = 30.864,37 → abgerundet
    assert income_tax(D("300000")) == D("115529")  # 0,45 × 300.000 − 19.470,38


@settings(max_examples=300, deadline=None)
@given(st.integers(0, 400_000))
def test_income_tax_is_monotonic(x: int) -> None:
    assert income_tax(D(x)) <= income_tax(D(x + 100))


def test_salary_breakdown_is_consistent() -> None:
    s = salary(D("4000"), sv_status="pflichtig", childless=True)
    assert s.net_month == s.gross_month - s.lst_month - s.solz_month - s.church_month - s.sv_employee_month
    assert s.employer_cost_month == s.gross_month + s.sv_employer_month + s.levies_month
    assert D("0.19") < s.sv_employee_month / s.gross_month < D("0.23")   # ca. 21 % AN-Anteil
    assert s.lst_month > 0 and s.solz_month == 0                          # Soli erst ab hoher Lohnsteuer


def test_contribution_ceilings() -> None:
    a = salary(D("9000"))
    b = salary(D("12000"))
    assert a.sv_employee_month == b.sv_employee_month, "oberhalb der BBG keine höheren SV-Beiträge"


def test_sv_free_md() -> None:
    s = salary(D("4000"), sv_status="frei")
    assert s.sv_employee_month == 0 and s.sv_employer_month == 0 and s.employer_cost_month == D("4000")


def test_church_tax() -> None:
    assert salary(D("4000"), church=True).church_month > 0


def test_gross_for_employer_cost_inverts_salary() -> None:
    gross = gross_for_employer_cost(D("5000"))
    assert salary(gross).employer_cost_month <= D("5000") < salary(gross + D("0.05")).employer_cost_month


def test_distribution_abgeltungsteuer() -> None:
    d = distribution(D("10000"))
    assert (d.kapest, d.solz, d.net) == (D("2500.00"), D("137.50"), D("7362.50"))   # 26,375 %
    c = distribution(D("10000"), church=True)
    assert c.kapest == D("2444.99") and c.church == D("220.05")


# ───────────────────────── Steuerklassen & Profil ─────────────────────────

from app.domain.payroll import TaxProfile, solidarity, wage_tax_year  # noqa: E402


def test_tax_class_ordering() -> None:
    zve = Decimal("50000")
    t1, t2, t3, t5, t6 = (wage_tax_year(zve, k) for k in (1, 2, 3, 5, 6))
    assert t3 < t2 < t1 < t5 <= t6
    assert t3 == income_tax(zve / 2) * 2, "Splitting"
    assert wage_tax_year(Decimal("10000"), 5) >= Decimal("1400"), "Klasse V: mindestens 14 %"


def test_solidarity_zone() -> None:
    assert solidarity(Decimal("20000"), 1) == 0
    just_over = solidarity(Decimal("21000"), 1)
    assert 0 < just_over < Decimal("21000") * Decimal("0.055"), "Milderungszone"
    assert solidarity(Decimal("60000"), 1) == Decimal("3300.00")
    assert solidarity(Decimal("30000"), 3) == 0, "doppelte Freigrenze bei Klasse III"


def test_profile_effects() -> None:
    gross = Decimal("6000")
    base = salary(gross, profile=TaxProfile())
    church = salary(gross, profile=TaxProfile(church_tax=True))
    church_by = salary(gross, profile=TaxProfile(church_tax=True, state="BY"))
    kids = salary(gross, profile=TaxProfile(church_tax=True, child_allowances=Decimal("2"), children_under_25=2))
    assert church.church_month > church_by.church_month > 0, "9 % NRW vs. 8 % Bayern"
    assert kids.lst_month >= base.lst_month, "geringere PV-Beiträge → kleinere Vorsorgepauschale → etwas mehr Lohnsteuer"
    assert kids.church_month < church.church_month, "Kinderfreibeträge mindern die Kirchensteuer"
    assert kids.sv_employee_month < base.sv_employee_month, "kein Kinderlosenzuschlag + Abschlag ab 2. Kind"
    young = salary(gross, profile=TaxProfile(birth_year=2005))
    assert young.sv_employee_month < base.sv_employee_month, "unter 23: kein Kinderlosenzuschlag"
    cheap_kk = salary(gross, profile=TaxProfile(kv_additional_rate=Decimal("1.5")))
    assert cheap_kk.sv_employee_month < base.sv_employee_month
    pkv = salary(gross, profile=TaxProfile(health_insurance="pkv", pkv_premium=Decimal("700")))
    assert pkv.sv_employer_month < base.sv_employer_month + Decimal("350.01")
    assert pkv.employer_cost_month - pkv.gross_month - pkv.levies_month >= Decimal("350"), "Zuschuss in den AG-Kosten"
    class3 = salary(gross, profile=TaxProfile(tax_class=3))
    assert class3.lst_month < base.lst_month and class3.net_month > base.net_month
