"""Ertragsteuersätze je Jahr und Berechnung der Rücklagen (docs/konzept/01 §4).

⚠️ Körperschaftsteuer: beschlossene stufenweise Senkung ab 2028 (14 % … 10 % ab 2032) – vor Nutzung prüfen.
Gewerbesteuer: Messzahl 3,5 % × Hebesatz (Düsseldorf 440 %), kein Freibetrag für Kapitalgesellschaften,
Gewerbeertrag auf volle 100 € abgerundet. Hinzurechnungen (§ 8 GewStG) sind hier nicht berücksichtigt.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.core.money import ZERO, round_cent

KST_BY_YEAR = {2028: Decimal("14"), 2029: Decimal("13"), 2030: Decimal("12"), 2031: Decimal("11")}
SOLZ_RATE = Decimal("5.5")
GEWST_MESSZAHL = Decimal("3.5")
UG_RESERVE_RATE = Decimal("25")


def kst_rate(year: int) -> Decimal:
    if year >= 2032:
        return Decimal("10")
    return KST_BY_YEAR.get(year, Decimal("15"))


@dataclass(frozen=True, slots=True)
class IncomeTaxEstimate:
    profit_before_tax: Decimal          # handelsrechtlich: Erlöse − Aufwand
    taxable_income: Decimal             # steuerlich: Erlöse − abziehbarer Aufwand
    kst_rate: Decimal
    kst: Decimal
    solz: Decimal
    gewst_base: Decimal
    gewst: Decimal
    total: Decimal
    profit_after_tax: Decimal
    ug_reserve: Decimal                 # gesetzliche Rücklage (Ausschüttungssperre), keine Steuer
    tax_loss_used: Decimal = ZERO       # verrechneter steuerlicher Verlustvortrag (§ 10d EStG, § 10a GewStG)
    commercial_loss_used: Decimal = ZERO  # mit dem Jahresüberschuss verrechneter Verlustvortrag (§ 5a Abs. 3 GmbHG)


def estimate_income_taxes(*, year: int, revenue: Decimal, expense: Decimal, deductible: Decimal,
                          trade_tax_multiplier: Decimal, is_ug: bool, tax_loss_carryforward: Decimal = ZERO,
                          commercial_loss_carryforward: Decimal = ZERO) -> IncomeTaxEstimate:
    """Ertragsteuern und UG-Rücklage eines Jahres.

    Verlustvorträge: steuerlich bis 1 Mio. € unbeschränkt, darüber 70 % des übersteigenden Betrags (Mindestbesteuerung,
    § 10d Abs. 2 EStG 2024–2027; für die GewSt 60 %, § 10a GewStG). Für die UG-Rücklage wird der Jahresüberschuss um den
    Verlustvortrag des Vorjahres gemindert (§ 5a Abs. 3 GmbHG).
    """
    profit = revenue - expense
    raw_taxable = revenue - deductible
    tax_used = ZERO
    gewst_used = ZERO
    if raw_taxable > 0 and tax_loss_carryforward > 0:
        limit = Decimal("1000000")
        tax_used = min(tax_loss_carryforward, raw_taxable if raw_taxable <= limit else limit + (raw_taxable - limit) * Decimal("0.70"))
        gewst_used = min(tax_loss_carryforward, raw_taxable if raw_taxable <= limit else limit + (raw_taxable - limit) * Decimal("0.60"))
    taxable = raw_taxable - tax_used
    rate = kst_rate(year)
    if taxable <= 0:
        kst = solz = gewst = ZERO
        gewst_base = ZERO
    else:
        kst = round_cent(taxable * rate / 100)
        solz = round_cent(kst * SOLZ_RATE / 100)
        gewst_base = ((raw_taxable - gewst_used) / 100).to_integral_value(rounding=ROUND_DOWN) * 100
        gewst = round_cent(gewst_base * GEWST_MESSZAHL / 100 * trade_tax_multiplier / 100)
    total = kst + solz + gewst
    after = profit - total
    commercial_used = min(commercial_loss_carryforward, after) if after > 0 else ZERO
    reserve_base = after - commercial_used
    reserve = round_cent(reserve_base * UG_RESERVE_RATE / 100) if is_ug and reserve_base > 0 else ZERO
    return IncomeTaxEstimate(profit_before_tax=profit, taxable_income=taxable, kst_rate=rate, kst=kst, solz=solz,
                             gewst_base=gewst_base, gewst=gewst, total=total, profit_after_tax=after, ug_reserve=reserve,
                             tax_loss_used=tax_used, commercial_loss_used=commercial_used)
