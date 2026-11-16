"""Lohnsteuer, Solidaritätszuschlag und Kirchensteuer nach dem amtlichen Programmablaufplan (PAP) des BMF.

Der PAP wird aus der offiziellen XML-Datei erzeugt (tools/pap_codegen.py → app/domain/pap/lst<Jahr>.py) und ist gegen die
externe Prüfschnittstelle des BMF abgeglichen (tests/data/pap<Jahr>_bmf.json). Unterstützt: alle Steuerklassen inkl.
Faktorverfahren, Kinderfreibeträge, Freibetrag/Hinzurechnungsbetrag, Vorsorgepauschale (GKV/PKV, RV-/ALV-Freiheit),
Altersentlastungsbetrag und sonstige Bezüge (Einmalzahlungen) nach § 39b Abs. 3 EStG.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

D = Decimal
CENT = D("0.01")
ZERO = D(0)
KVZ_AVG = D("2.9")


def _pap(year: int) -> Any:
    if year == 2026:
        from app.domain.pap.lst2026 import Lohnsteuer2026

        return Lohnsteuer2026
    raise ValueError(f"Für {year} ist noch kein Programmablaufplan hinterlegt (tools/pap_codegen.py)")


def available_years() -> list[int]:
    return [2026]


def church_rate(state: str) -> D:
    return D(8) if state in ("BY", "BW") else D(9)


@dataclass(frozen=True, slots=True)
class WageTaxInput:
    year: int
    monthly_wage: D                   # laufender steuerpflichtiger Arbeitslohn des Monats
    tax_class: int = 1
    child_allowances: D = ZERO
    church: bool = False
    state: str = "NW"
    birth_year: int | None = None
    childless_surcharge: bool = False  # PV-Zuschlag für Kinderlose
    pv_children_reduction: int = 0     # Zahl der Abschläge (2. bis 5. Kind)
    health_insurance: str = "gkv"
    kv_additional_rate: D = KVZ_AVG
    pkv_premium: D = ZERO              # Basis-KV/PV-Beitrag (Monat)
    pkv_employer_subsidy: D = ZERO
    rv_free: bool = False              # nicht rentenversicherungspflichtig (z. B. beherrschende GF)
    alv_free: bool = False
    factor: D | None = None            # Faktorverfahren (nur Steuerklasse IV)
    allowance_month: D = ZERO          # ELStAM-Freibetrag
    addition_month: D = ZERO           # ELStAM-Hinzurechnungsbetrag
    one_time: D = ZERO                 # sonstige Bezüge in diesem Monat
    annual_regular: D | None = None    # voraussichtlicher Jahresarbeitslohn ohne diesen sonstigen Bezug
    prior_one_time: D = ZERO           # bereits gezahlte sonstige Bezüge dieses Jahres


@dataclass(frozen=True, slots=True)
class WageTax:
    lst: D
    solz: D
    church: D
    lst_one_time: D
    solz_one_time: D
    church_one_time: D

    @property
    def total(self) -> D:
        return self.lst + self.solz + self.church + self.lst_one_time + self.solz_one_time + self.church_one_time


def _cents(v: D) -> int:
    return int((v * 100).to_integral_value(rounding=ROUND_DOWN))


def wage_tax(i: WageTaxInput) -> WageTax:
    cls = _pap(i.year)
    alter1 = 1 if i.birth_year is not None and i.birth_year + 64 < i.year else 0
    params: dict[str, Any] = {
        "LZZ": 2, "RE4": _cents(i.monthly_wage), "STKL": i.tax_class, "ZKF": str(i.child_allowances if i.tax_class <= 4 else 0),
        "R": 1 if i.church else 0, "KVZ": f"{i.kv_additional_rate:.2f}", "PVZ": 1 if i.childless_surcharge else 0,
        "PVA": str(min(4, max(0, i.pv_children_reduction))), "PVS": 1 if i.state == "SN" else 0, "KRV": 1 if i.rv_free else 0,
        "ALV": 1 if i.alv_free else 0, "PKV": 1 if i.health_insurance == "pkv" else 0,
        "PKPV": _cents(i.pkv_premium) if i.health_insurance == "pkv" else 0,
        "PKPVAGZ": _cents(i.pkv_employer_subsidy) if i.health_insurance == "pkv" else 0,
        "LZZFREIB": _cents(i.allowance_month), "LZZHINZU": _cents(i.addition_month), "ALTER1": alter1,
        "AJAHR": (i.birth_year + 65) if alter1 and i.birth_year else 0,
        "af": 1 if i.factor is not None and i.tax_class == 4 else 0, "f": f"{i.factor:.3f}" if i.factor is not None else "1.0",
    }
    if i.one_time:
        params["SONSTB"] = _cents(i.one_time)
        annual = i.annual_regular if i.annual_regular is not None else i.monthly_wage * 12
        params["JRE4"] = _cents(annual + i.prior_one_time)
    r = cls(**params).calculate()
    rate = church_rate(i.state)

    def eur(v: Any) -> D:
        return (D(v) / 100).quantize(CENT)

    church = (D(r.BK) / 100 * rate / 100).quantize(CENT, rounding=ROUND_DOWN) if i.church else D(0)
    church_s = (D(r.BKS) / 100 * rate / 100).quantize(CENT, rounding=ROUND_DOWN) if i.church else D(0)
    return WageTax(eur(r.LSTLZZ), eur(r.SOLZLZZ), church, eur(r.STS), eur(r.SOLZS), church_s)
