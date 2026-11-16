"""Planungsrechnung Geschäftsführergehalt und Gewinnausschüttung (docs/konzept/11).

⚠️ NÄHERUNG für die Planung – verbindlich ist die Lohnabrechnung (Steuerberater/Lohnbüro).
- Lohnsteuer: Einkommensteuertarif § 32a EStG (Parameter 2026) auf das hochgerechnete Jahresbrutto abzüglich
  Arbeitnehmer-Pauschbetrag, Sonderausgaben-Pauschbetrag und vereinfachter Vorsorgepauschale (AN-Sozialbeiträge).
  Steuerklassen: I/IV Grundtarif, II mit Entlastungsbetrag für Alleinerziehende, III Splittingverfahren,
  V/VI nach § 39b Abs. 2 Satz 7 EStG (doppelter Unterschiedsbetrag, mindestens 14 %); VI ohne Pauschbeträge.
  Kinderfreibeträge wirken nur auf Solidaritätszuschlag und Kirchensteuer (§ 51a EStG).
- Sozialversicherung: Beitragssätze und Beitragsbemessungsgrenzen 2026, Zusatzbeitrag/Umlagen konfigurierbar.
Alle Parameter vor Nutzung mit den amtlichen Werten des Jahres abgleichen.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Literal

D = Decimal
ZERO_D = Decimal(0)
SvStatus = Literal["pflichtig", "frei"]


@dataclass(frozen=True, slots=True)
class PayrollParams:
    year: int = 2026
    # Einkommensteuertarif § 32a EStG
    basic_allowance: D = D("12348")          # Grundfreibetrag
    zone2_end: D = D("17799")
    zone3_end: D = D("69878")
    zone4_end: D = D("277825")
    employee_allowance: D = D("1230")        # Arbeitnehmer-Pauschbetrag
    special_expenses_allowance: D = D("36")  # Sonderausgaben-Pauschbetrag
    solz_free_limit: D = D("20350")          # Freigrenze Jahres-Lohnsteuer (ledig) ⚠️
    church_tax_rate: D = D("9")              # NRW (BY/BW: 8 %)
    child_allowance_full: D = D("9756")      # Kinderfreibetrag + BEA je vollem Zähler 1,0 ⚠️
    single_parent_relief: D = D("4260")      # Entlastungsbetrag Steuerklasse II ⚠️
    pv_child_reduction: D = D("0.25")        # Abschlag je Kind unter 25 ab dem 2. bis 5. Kind (AN)
    pv_saxony_extra_employee: D = D("0.5")   # Sachsen: höherer AN-Anteil
    # Sozialversicherung (Arbeitnehmer-/Arbeitgeberanteil je Hälfte, außer Hinweis)
    rv_rate: D = D("18.6")
    av_rate: D = D("2.6")
    kv_rate: D = D("14.6")
    kv_additional_rate: D = D("2.9")         # durchschnittlicher Zusatzbeitrag ⚠️ kassenindividuell
    pv_rate: D = D("3.6")
    pv_childless_surcharge: D = D("0.6")     # nur Arbeitnehmer
    bbg_rv_month: D = D("8450")              # Beitragsbemessungsgrenze RV/AV
    bbg_kv_month: D = D("5812.50")           # Beitragsbemessungsgrenze KV/PV
    insolvency_levy: D = D("0.15")           # U3 (Arbeitgeber) ⚠️
    u1_rate: D = D("0")                      # Umlage U1 (kassenabhängig, ggf. nicht verpflichtet)
    u2_rate: D = D("0.22")                   # Umlage U2 ⚠️ kassenabhängig
    capital_gains_rate: D = D("25")          # Abgeltungsteuer
    solz_rate: D = D("5.5")


PARAMS_2026 = PayrollParams()


def _cent(x: D) -> D:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def income_tax(zve: D, p: PayrollParams = PARAMS_2026) -> D:
    """Jahres-Einkommensteuer nach § 32a EStG (Grundtarif), auf volle Euro abgerundet."""
    x = zve.to_integral_value(rounding=ROUND_DOWN)
    if x <= p.basic_allowance:
        tax = D(0)
    elif x <= p.zone2_end:
        y = (x - p.basic_allowance) / 10000
        tax = (D("914.51") * y + 1400) * y
    elif x <= p.zone3_end:
        z = (x - p.zone2_end) / 10000
        tax = (D("173.10") * z + 2397) * z + D("1034.87")
    elif x <= p.zone4_end:
        tax = D("0.42") * x - D("11135.63")
    else:
        tax = D("0.45") * x - D("19470.38")
    return tax.to_integral_value(rounding=ROUND_DOWN)


@dataclass(frozen=True, slots=True)
class TaxProfile:
    """Persönliche Lohnsteuer- und SV-Merkmale (ELStAM / Krankenkasse)."""

    tax_class: int = 1
    child_allowances: D = D(0)          # Zahl der Kinderfreibeträge (0, 0.5, 1, 1.5 …)
    church_tax: bool = False
    state: str = "NW"                   # Bundesland (Kirchensteuersatz, Pflegeversicherung Sachsen)
    birth_year: int | None = None
    children_under_25: int = 0
    health_insurance: Literal["gkv", "pkv"] = "gkv"
    kv_additional_rate: D | None = None  # kassenindividueller Zusatzbeitrag
    pkv_premium: D = D(0)                # Monatsbeitrag PKV inkl. PPV
    factor: D | None = None              # Faktorverfahren (Steuerklasse IV)
    allowance_month: D = D(0)            # ELStAM-Freibetrag je Monat
    addition_month: D = D(0)             # ELStAM-Hinzurechnungsbetrag je Monat

    def childless_surcharge(self, year: int) -> bool:
        too_young = self.birth_year is not None and year - self.birth_year < 23
        return self.children_under_25 == 0 and not too_young


def wage_tax_year(taxable_wage: D, tax_class: int, p: PayrollParams = PARAMS_2026) -> D:
    """Jahreslohnsteuer je Steuerklasse auf das zu versteuernde Jahreseinkommen (vor Pauschbeträgen bei VI)."""
    if tax_class == 3:
        return income_tax(taxable_wage / 2, p) * 2
    if tax_class in (5, 6):
        x = taxable_wage.to_integral_value(rounding=ROUND_DOWN)
        diff = 2 * (income_tax(x * D("1.25"), p) - income_tax(x * D("0.75"), p))
        return max(diff, (x * D("0.14")).to_integral_value(rounding=ROUND_DOWN))
    if tax_class == 2:
        return income_tax(max(D(0), taxable_wage - p.single_parent_relief), p)
    return income_tax(taxable_wage, p)


def solidarity(tax: D, tax_class: int, p: PayrollParams = PARAMS_2026) -> D:
    """Solidaritätszuschlag mit Freigrenze und Milderungszone (11,9 % des Überschreitungsbetrags)."""
    limit = p.solz_free_limit * (2 if tax_class == 3 else 1)
    if tax <= limit:
        return D(0)
    return _cent(min(tax * p.solz_rate / 100, (tax - limit) * D("11.9") / 100))


@dataclass(frozen=True, slots=True)
class SalaryResult:
    gross_month: D
    lst_month: D
    solz_month: D
    church_month: D
    sv_employee_month: D
    net_month: D
    sv_employer_month: D
    levies_month: D
    employer_cost_month: D
    employer_cost_year: D


def salary(gross_month: D, *, salaries_per_year: int = 12, sv_status: SvStatus = "pflichtig", church: bool = False,
           childless: bool = True, profile: TaxProfile | None = None, p: PayrollParams = PARAMS_2026) -> SalaryResult:
    if gross_month < 0:
        raise ValueError("Bruttogehalt darf nicht negativ sein")
    prof = profile or TaxProfile(church_tax=church, children_under_25=0 if childless else 1)
    rv_base = min(gross_month, p.bbg_rv_month)
    kv_base = min(gross_month, p.bbg_kv_month)
    kv_add = prof.kv_additional_rate if prof.kv_additional_rate is not None else p.kv_additional_rate
    pkv_subsidy = D(0)
    if sv_status == "pflichtig":
        rv = rv_base * p.rv_rate / 200
        av = rv_base * p.av_rate / 200
        if prof.health_insurance == "gkv":
            kv = kv_base * (p.kv_rate + kv_add) / 200
            pv_er = kv_base * p.pv_rate / 200
            pv_ee_rate = p.pv_rate / 2
            if prof.state == "SN":
                pv_er -= kv_base * p.pv_saxony_extra_employee / 100
                pv_ee_rate += p.pv_saxony_extra_employee
            if prof.childless_surcharge(p.year):
                pv_ee_rate += p.pv_childless_surcharge
            elif prof.children_under_25 >= 2:
                pv_ee_rate -= p.pv_child_reduction * (min(prof.children_under_25, 5) - 1)
            pv_ee = kv_base * pv_ee_rate / 100
        else:
            # PKV: Arbeitgeberzuschuss = halber Beitrag, höchstens die Hälfte des GKV-Höchstbeitrags (KV + PV)
            kv = pv_ee = pv_er = D(0)
            cap = kv_base * (p.kv_rate + p.kv_additional_rate + p.pv_rate) / 200
            pkv_subsidy = _cent(min(prof.pkv_premium / 2, cap))
        sv_ee = _cent(rv) + _cent(av) + _cent(kv) + _cent(pv_ee)
        sv_er = _cent(rv) + _cent(av) + _cent(kv) + _cent(pv_er) + pkv_subsidy
        levies = _cent(rv_base * (p.insolvency_levy + p.u1_rate + p.u2_rate) / 100)
    else:
        # SV-frei (z. B. beherrschende Gesellschafter-GF): Kranken-/Pflegeversicherung privat bzw. freiwillig
        sv_ee = sv_er = levies = D(0)
    annual_gross = gross_month * salaries_per_year
    own_premiums = (prof.pkv_premium - pkv_subsidy) * 12 if prof.health_insurance == "pkv" else D(0)
    vorsorge = sv_ee * 12 + own_premiums  # vereinfachte Vorsorgepauschale ⚠️
    allowances = D(0) if prof.tax_class == 6 else p.employee_allowance + p.special_expenses_allowance
    zve = max(D(0), annual_gross - allowances - vorsorge)
    lst_year = wage_tax_year(zve, prof.tax_class, p)
    # Zuschlagsteuern auf Basis der Lohnsteuer mit Kinderfreibeträgen (§ 51a Abs. 2a EStG)
    zve_children = max(D(0), zve - prof.child_allowances * p.child_allowance_full)
    base_tax = wage_tax_year(zve_children, prof.tax_class, p) if prof.child_allowances else lst_year
    solz_year = solidarity(base_tax, prof.tax_class, p)
    church_rate = D("8") if prof.state in ("BY", "BW") else p.church_tax_rate
    church_year = _cent(base_tax * church_rate / 100) if prof.church_tax else D(0)
    factor = D(salaries_per_year)
    lst = _cent(lst_year / factor)
    solz = _cent(solz_year / factor)
    ch = _cent(church_year / factor)
    net = gross_month - lst - solz - ch - sv_ee + pkv_subsidy - (prof.pkv_premium if prof.health_insurance == "pkv" else D(0))
    cost = gross_month + sv_er + levies
    return SalaryResult(gross_month, lst, solz, ch, sv_ee, net, sv_er, levies, cost, _cent(cost * factor))


def gross_for_employer_cost(target_cost_month: D, **kwargs: object) -> D:
    """Bruttogehalt, das (höchstens) die angegebenen Arbeitgeberkosten verursacht (Bisektion auf Cent)."""
    if target_cost_month <= 0:
        return D(0)
    lo, hi = D(0), target_cost_month
    for _ in range(60):
        mid = _cent((lo + hi) / 2)
        if salary(mid, **kwargs).employer_cost_month <= target_cost_month:  # type: ignore[arg-type]
            lo = mid
        else:
            hi = mid
        if hi - lo <= D("0.01"):
            break
    return lo


@dataclass(frozen=True, slots=True)
class DistributionResult:
    gross: D
    kapest: D
    solz: D
    church: D
    net: D


def distribution(gross: D, *, church: bool = False, p: PayrollParams = PARAMS_2026) -> DistributionResult:
    """Gewinnausschüttung an eine natürliche Person (Abgeltungsteuer, ohne Sparer-Pauschbetrag)."""
    if gross <= 0:
        return DistributionResult(D(0), D(0), D(0), D(0), D(0))
    if church:
        # Kirchensteuer mindert die Abgeltungsteuer: KapESt = Ertrag / (4 + KiSt-Satz)
        kapest = _cent(gross / (4 + p.church_tax_rate / 100))
        ch = _cent(kapest * p.church_tax_rate / 100)
    else:
        kapest = _cent(gross * p.capital_gains_rate / 100)
        ch = D(0)
    solz = _cent(kapest * p.solz_rate / 100)
    return DistributionResult(gross, kapest, solz, ch, gross - kapest - solz - ch)


# ───────────────────────── Gehaltsabrechnung mit Einzelpositionen ─────────────────────────

EmploymentType = Literal["regular", "minijob", "werkstudent", "shareholder"]
MINIJOB_LIMIT_2026 = D("603")       # Geringfügigkeitsgrenze 2026 (Mindestlohn 13,90 € × 130 / 3)
MINIMUM_WAGE_2026 = D("13.90")
MIDIJOB_UPPER_2026 = D("2000")
MINIJOB_RV_ER = D("15")             # Pauschalbeitrag RV Arbeitgeber
MINIJOB_RV_TOTAL = D("18.6")        # Eigenanteil AN = 3,6 % (ohne Befreiung)
MINIJOB_KV_ER = D("13")             # Pauschalbeitrag KV bei gesetzlich Versicherten
MINIJOB_FLAT_TAX = D("2")           # einheitliche Pauschsteuer (inkl. Soli/KiSt)
MINIJOB_U1 = D("0.8")
MINIJOB_U2 = D("0.22")


def _de(v: D) -> str:
    return str(v).replace(".", ",")


@dataclass(frozen=True, slots=True)
class MidijobBases:
    factor: D          # Faktor F
    total_base: D      # beitragspflichtige Einnahme gesamt (BE)
    employee_base: D   # beitragspflichtige Einnahme für den AN-Anteil


def midijob_bases(gross: D, p: PayrollParams = PARAMS_2026, *, lower: D = MINIJOB_LIMIT_2026,
                  upper: D = MIDIJOB_UPPER_2026) -> MidijobBases | None:
    """Übergangsbereich § 20a SGB IV (seit 2023): None außerhalb (Minijob-Grenze < Entgelt ≤ 2.000 €).

    F = 28 % / Gesamtsozialversicherungsbeitragssatz (RV + AV + KV allgemein + durchschnittl. Zusatzbeitrag + PV), auf 4 Stellen.
    BE    = F × G + ([OG / (OG − G)] − [G / (OG − G)] × F) × (AE − G)
    BE_AN = OG / (OG − G) × (AE − G)
    """
    if not (lower < gross <= upper):
        return None
    total_rate = p.rv_rate + p.av_rate + p.kv_rate + p.kv_additional_rate + p.pv_rate
    f = (D(28) / total_rate).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
    span = upper - lower
    be = f * lower + (upper / span - lower / span * f) * (gross - lower)
    be_an = upper / span * (gross - lower)
    return MidijobBases(f, _cent(be), _cent(be_an))


PERSON_GROUPS = {"regular": ("101", "sozialversicherungspflichtig Beschäftigte"),
                 "minijob": ("109", "geringfügig entlohnte Beschäftigte"),
                 "werkstudent": ("106", "Werkstudenten")}


def contribution_group_key(kind: str, *, health_insurance: str = "gkv", rv_exempt: bool = False, sv_free: bool = False) -> str:
    """Beitragsgruppenschlüssel KV-RV-AV-PV (DEÜV), z. B. 1111 = alle Zweige allgemein, 6100 = Minijob mit RV-Pflicht."""
    if sv_free or kind == "shareholder":
        return "0000"
    if kind == "minijob":
        return ("6" if health_insurance == "gkv" else "0") + ("5" if rv_exempt else "1") + "00"
    if kind == "werkstudent":
        return "0" + ("0" if rv_exempt else "1") + "00"
    kv, pv = ("1", "1") if health_insurance == "gkv" else ("0", "0")
    return kv + ("0" if rv_exempt else "1") + "1" + pv


@dataclass(frozen=True, slots=True)
class Payslip:
    gross: D
    lst: D
    solz: D
    church: D
    rv_ee: D
    av_ee: D
    kv_ee: D
    pv_ee: D
    rv_er: D
    av_er: D
    kv_er: D
    pv_er: D
    levies: D
    flat_tax: D
    pkv_subsidy: D
    pkv_premium: D
    net: D
    payout: D
    employer_cost: D
    notes: tuple[str, ...]
    one_time: D = D(0)        # sonstige Bezüge (Einmalzahlung)
    benefits: D = D(0)        # geldwerte Vorteile/Sachbezüge (steuer- und SV-pflichtig, nicht ausgezahlt)
    tax_free: D = D(0)        # steuer- und SV-freie Zahlungen (z. B. Erstattungen)
    deductions: D = D(0)      # Nettoabzüge (Vorschuss, Pfändung …)
    sv_base_rv: D = D(0)
    sv_base_kv: D = D(0)

    @property
    def sv_ee(self) -> D:
        return self.rv_ee + self.av_ee + self.kv_ee + self.pv_ee

    @property
    def sv_er(self) -> D:
        return self.rv_er + self.av_er + self.kv_er + self.pv_er

    @property
    def taxes(self) -> D:
        return self.lst + self.solz + self.church


def _taxes(gross: D, sv_ee_month: D, salaries_per_year: int, prof: TaxProfile, pkv_own: D, p: PayrollParams) -> tuple[D, D, D]:
    annual_gross = gross * salaries_per_year
    vorsorge = sv_ee_month * 12 + pkv_own * 12  # vereinfachte Vorsorgepauschale ⚠️
    allowances = D(0) if prof.tax_class == 6 else p.employee_allowance + p.special_expenses_allowance
    zve = max(D(0), annual_gross - allowances - vorsorge)
    lst_year = wage_tax_year(zve, prof.tax_class, p)
    zve_children = max(D(0), zve - prof.child_allowances * p.child_allowance_full)
    base_tax = wage_tax_year(zve_children, prof.tax_class, p) if prof.child_allowances else lst_year
    solz_year = solidarity(base_tax, prof.tax_class, p)
    church_rate = D("8") if prof.state in ("BY", "BW") else p.church_tax_rate
    church_year = _cent(base_tax * church_rate / 100) if prof.church_tax else D(0)
    f = D(salaries_per_year)
    return _cent(lst_year / f), _cent(solz_year / f), _cent(church_year / f)


def payslip(gross: D, kind: EmploymentType, *, profile: TaxProfile, salaries_per_year: int = 12, rv_exempt: bool = False,
            sv_free: bool = False, p: PayrollParams = PARAMS_2026, one_time: D = ZERO_D, benefits: D = ZERO_D, tax_free: D = ZERO_D,
            deductions: D = ZERO_D, annual_regular: D | None = None, prior_one_time: D = ZERO_D,
            sv_room: tuple[D, D] | None = None) -> Payslip:
    """Monatliche Entgeltabrechnung mit allen Zweigen (AN/AG); Lohnsteuer nach amtlichem PAP.

    ``gross`` = laufender Barlohn; ``benefits`` = laufende Sachbezüge (steuer-/SV-pflichtig, werden vom Netto wieder abgezogen);
    ``one_time`` = sonstiger Bezug (Lohnsteuer nach § 39b Abs. 3 EStG, SV bis zur anteiligen Jahres-BBG ``sv_room`` (RV, KV));
    ``tax_free`` = steuer- und SV-freie Zahlungen (Auszahlung); ``deductions`` = Nettoabzüge (Vorschuss, Pfändung …).
    """
    if gross < 0 or one_time < 0 or benefits < 0:
        raise ValueError("Bruttolohn darf nicht negativ sein")
    z = D(0)
    notes: list[str] = []
    regular = gross + benefits
    total = regular + one_time
    if kind == "minijob":
        if regular > MINIJOB_LIMIT_2026:
            raise ValueError(f"Minijob über der Geringfügigkeitsgrenze ({MINIJOB_LIMIT_2026} €) – als Midijob/regulär abrechnen")
        rv_er = _cent(total * MINIJOB_RV_ER / 100)
        rv_ee = z if rv_exempt else _cent(total * (MINIJOB_RV_TOTAL - MINIJOB_RV_ER) / 100)
        kv_er = _cent(total * MINIJOB_KV_ER / 100) if profile.health_insurance == "gkv" else z
        flat = _cent(total * MINIJOB_FLAT_TAX / 100)
        levies = _cent(total * (MINIJOB_U1 + MINIJOB_U2 + p.insolvency_levy) / 100)
        net = total - rv_ee
        cost = total + rv_er + kv_er + flat + levies + tax_free
        notes.append("Minijob: Meldung und Beiträge an die Minijob-Zentrale (Knappschaft-Bahn-See)")
        if one_time:
            notes.append("Einmalzahlung im Minijob: Jahresgrenze (12 × 603 € = 7.236 €) beachten")
        return Payslip(total, z, z, z, rv_ee, z, z, z, rv_er, z, kv_er, z, levies, flat, z, z, net,
                       net - benefits + tax_free - deductions, cost, tuple(notes), one_time, benefits, tax_free, deductions, total, total)
    sv_month = regular + one_time if regular <= MIDIJOB_UPPER_2026 else regular
    midi = midijob_bases(sv_month, p) if kind == "regular" and not sv_free else None
    room_rv, room_kv = sv_room if sv_room is not None else (p.bbg_rv_month - min(regular, p.bbg_rv_month),
                                                             p.bbg_kv_month - min(regular, p.bbg_kv_month))
    rv_base = min(regular, p.bbg_rv_month) + min(one_time, max(z, room_rv))
    kv_base = min(regular, p.bbg_kv_month) + min(one_time, max(z, room_kv))
    if one_time and (one_time > room_rv or one_time > room_kv):
        notes.append("Einmalzahlung: nur bis zur anteiligen Jahres-Beitragsbemessungsgrenze beitragspflichtig "
                     "(Januar–März ggf. Märzklausel prüfen)")
    kv_add = profile.kv_additional_rate if profile.kv_additional_rate is not None else p.kv_additional_rate
    rv_ee = rv_er = av_ee = av_er = kv_ee = kv_er = pv_ee = pv_er = levies = pkv_subsidy = z

    def split(rate: D, base: D | None = None) -> tuple[D, D]:
        base = rv_base if base is None else base
        """AN/AG-Anteil eines Zweigs mit Gesamtsatz ``rate`` (je Hälfte; im Übergangsbereich nach § 20a SGB IV)."""
        if midi is None:
            half = _cent(base * rate / 200)
            return half, half
        ee = _cent(midi.employee_base * rate / 200)
        return ee, _cent(midi.total_base * rate / 100) - ee

    if midi is not None:
        notes.append(f"Übergangsbereich (Midijob, § 20a SGB IV): beitragspflichtige Einnahme {_de(midi.total_base)} €, "
                     f"davon AN-Anteil aus {_de(midi.employee_base)} € (Faktor F {_de(midi.factor)})")
    if not sv_free:
        rv_ee, rv_er = split(p.rv_rate)
        # Umlagen U1/U2/Insolvenzgeld immer vom tatsächlichen Arbeitsentgelt
        levies = _cent(min(total, p.bbg_rv_month + one_time) * (p.insolvency_levy + p.u1_rate + p.u2_rate) / 100)
        if kind == "werkstudent":
            notes.append("Werkstudent: nur Rentenversicherung; Kranken-/Pflegeversicherung über die studentische Versicherung")
        else:
            av_ee, av_er = split(p.av_rate)
            if profile.health_insurance == "gkv":
                kv_ee, kv_er = split(p.kv_rate + kv_add, kv_base)
                ee_base, tot_base = (kv_base, kv_base) if midi is None else (midi.employee_base, midi.total_base)
                pv_ee_rate = p.pv_rate / 2
                saxony = p.pv_saxony_extra_employee if profile.state == "SN" else z
                pv_ee_rate += saxony
                # AG-Anteil: Hälfte des Regelsatzes (Sachsen weniger); Zuschläge/Abschläge trägt allein der AN
                pv_er_raw = tot_base * p.pv_rate / 100 - ee_base * (p.pv_rate / 2 + saxony) / 100
                if profile.childless_surcharge(p.year):
                    pv_ee_rate += p.pv_childless_surcharge
                elif profile.children_under_25 >= 2:
                    pv_ee_rate -= p.pv_child_reduction * (min(profile.children_under_25, 5) - 1)
                pv_ee, pv_er = _cent(ee_base * pv_ee_rate / 100), _cent(pv_er_raw)
            else:
                cap = kv_base * (p.kv_rate + p.kv_additional_rate + p.pv_rate) / 200
                pkv_subsidy = _cent(min(profile.pkv_premium / 2, cap))
    pkv_premium = profile.pkv_premium if profile.health_insurance == "pkv" else z
    sv_ee = rv_ee + av_ee + kv_ee + pv_ee
    from app.domain.lohnsteuer import WageTaxInput, wage_tax

    tax = wage_tax(WageTaxInput(
        year=p.year, monthly_wage=regular, tax_class=profile.tax_class, child_allowances=profile.child_allowances,
        church=profile.church_tax, state=profile.state, birth_year=profile.birth_year,
        childless_surcharge=profile.childless_surcharge(p.year) and not sv_free and kind != "werkstudent",
        pv_children_reduction=max(0, min(profile.children_under_25, 5) - 1) if profile.children_under_25 >= 2 else 0,
        health_insurance=profile.health_insurance, kv_additional_rate=kv_add, pkv_premium=pkv_premium, pkv_employer_subsidy=pkv_subsidy,
        rv_free=sv_free or rv_exempt, alv_free=sv_free or kind == "werkstudent", factor=profile.factor,
        allowance_month=profile.allowance_month, addition_month=profile.addition_month, one_time=one_time,
        annual_regular=annual_regular, prior_one_time=prior_one_time))
    lst, solz, church = tax.lst + tax.lst_one_time, tax.solz + tax.solz_one_time, tax.church + tax.church_one_time
    if one_time:
        notes.append(f"Sonstiger Bezug {one_time} €: Lohnsteuer {tax.lst_one_time} € nach § 39b Abs. 3 EStG (Jahreslohnverfahren)")
    net = total - lst - solz - church - sv_ee
    # Sachbezüge sind schon ausgegeben (vom Netto abziehen); PKV-Zuschuss und steuerfreie Zahlungen werden mit ausgezahlt
    payout = net - benefits + pkv_subsidy + tax_free - deductions
    cost = total + rv_er + av_er + kv_er + pv_er + levies + pkv_subsidy + tax_free
    return Payslip(total, lst, solz, church, rv_ee, av_ee, kv_ee, pv_ee, rv_er, av_er, kv_er, pv_er, levies, z, pkv_subsidy,
                   pkv_premium, net, payout, cost, tuple(notes), one_time, benefits, tax_free, deductions,
                   rv_base if not sv_free else z, kv_base if not sv_free and kind != "werkstudent" else z)
