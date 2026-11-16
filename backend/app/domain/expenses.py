"""Ausgaben: Kategorien und steuerliche Berechnung je Beleg (docs/konzept/03 und 01 §3.2).

Für jeden Beleg werden aus Nettobetrag, ausgewiesener Steuer und Steuerfall abgeleitet:
- ``input_tax``   abziehbare Vorsteuer (UStVA Kz 66 bzw. 67/61/62)
- ``rc_tax``      selbst geschuldete Umsatzsteuer bei Reverse Charge (Kz 47/85/89-Steuer)
- ``expense``     Aufwand in der Gewinnermittlung (netto, oder brutto wenn Vorsteuer nicht abziehbar)
- ``deductible``  davon steuerlich abziehbarer Betriebsausgabenanteil (z. B. Bewirtung 70 %)

⚠️ Kontenzuordnung (SKR04) ist ein Vorschlag und mit dem Steuerberater abzustimmen.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.money import ZERO, round_cent
from app.domain.tax_cases import Direction, get_tax_case

TOLERANCE = Decimal("0.05")
BEWIRTUNG_DEDUCTIBLE = Decimal("0.70")


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    label: str
    skr04: str


CATEGORIES: tuple[Category, ...] = (
    Category("software", "Software & Lizenzen", "6837"),
    Category("hosting", "Server, Hosting & IT-Dienste", "6495"),
    Category("telecom", "Telefon & Internet", "6805"),
    Category("advertising", "Werbung & Marketing", "6600"),
    Category("office", "Bürobedarf", "6815"),
    Category("hardware_gwg", "Hardware (GWG bis 800 € netto)", "6260"),
    Category("fixed_asset", "Anlagevermögen (über 800 € netto)", "0690"),
    Category("rent", "Miete & Raumkosten", "6310"),
    Category("training", "Fortbildung", "6821"),
    Category("literature", "Fachliteratur & Zeitschriften", "6820"),
    Category("consulting", "Rechts- & Beratungskosten", "6825"),
    Category("bookkeeping", "Buchführung & Steuerberatung", "6830"),
    Category("insurance", "Versicherungen", "6400"),
    Category("fees", "Beiträge & Gebühren", "6420"),
    Category("bank_fees", "Bankgebühren", "6855"),
    Category("travel", "Reisekosten", "6670"),
    Category("hospitality", "Bewirtung", "6640"),
    Category("gifts", "Geschenke", "6610"),
    Category("freelancer", "Fremdleistungen", "5900"),
    Category("other", "Sonstige betriebliche Aufwendungen", "6300"),
)
_BY_CODE = {c.code: c for c in CATEGORIES}

DOMESTIC_WITH_TAX = frozenset({"IN_DE_19", "IN_DE_7", "IN_EINFUHR", "IN_BEWIRTUNG"})
REVERSE_CHARGE = frozenset({"IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB"})
NO_INPUT_TAX = frozenset({"IN_OHNE_VST", "IN_NICHT_ABZIEHBAR"})


class ExpenseError(ValueError):
    pass


def get_category(code: str) -> Category:
    try:
        return _BY_CODE[code]
    except KeyError:
        raise ExpenseError(f"Unbekannte Kategorie: {code}") from None


@dataclass(frozen=True, slots=True)
class ExpenseAmounts:
    net: Decimal
    tax: Decimal
    gross: Decimal
    rc_tax: Decimal
    input_tax: Decimal
    expense: Decimal
    deductible: Decimal
    warnings: tuple[str, ...]


def compute_expense(tax_case_code: str, net: Decimal, tax_on_document: Decimal, category: str) -> ExpenseAmounts:
    """Berechnet die steuerlichen Werte eines Belegs (Beträge in Euro, 2 Nachkommastellen)."""
    get_category(category)
    case = get_tax_case(tax_case_code)
    if case.direction is not Direction.INCOMING or not case.active:
        raise ExpenseError(f"Steuerfall {tax_case_code} ist kein Eingangs-Steuerfall")
    if net != round_cent(net) or tax_on_document != round_cent(tax_on_document):
        raise ExpenseError("Beträge mit höchstens 2 Nachkommastellen angeben")
    if net == 0:
        raise ExpenseError("Nettobetrag darf nicht 0 sein")
    if tax_on_document != 0 and (tax_on_document > 0) != (net > 0):
        raise ExpenseError("Steuer und Nettobetrag müssen dasselbe Vorzeichen haben")

    warnings: list[str] = []
    rate = case.rate / Decimal(100)
    expected = round_cent(net * rate)
    gross = net + tax_on_document
    rc_tax = ZERO
    input_tax = ZERO

    if tax_case_code in DOMESTIC_WITH_TAX:
        if abs(tax_on_document - expected) > TOLERANCE:
            warnings.append(
                f"Ausgewiesene Steuer {tax_on_document} € weicht von {case.rate} % auf den Nettobetrag "
                f"({expected} €) ab – Beleg und Steuerfall prüfen."
            )
        input_tax = tax_on_document
        expense = net
    elif tax_case_code in REVERSE_CHARGE:
        if tax_on_document != 0:
            raise ExpenseError(
                "Bei Reverse Charge darf auf dem Beleg keine Umsatzsteuer ausgewiesen sein – "
                "weist der Lieferant USt aus, ist der Steuerfall falsch (z. B. USt-IdNr beim Anbieter hinterlegen)."
            )
        rc_tax = expected
        input_tax = expected
        expense = net
    elif tax_case_code in NO_INPUT_TAX:
        expense = gross  # nicht abziehbare Steuer erhöht den Aufwand
    else:  # pragma: no cover – alle aktiven Eingangsfälle sind oben abgedeckt
        raise ExpenseError(f"Steuerfall {tax_case_code} nicht unterstützt")

    if tax_case_code == "IN_NICHT_ABZIEHBAR":
        deductible = ZERO
    elif tax_case_code == "IN_BEWIRTUNG" or category == "hospitality":
        deductible = round_cent(expense * BEWIRTUNG_DEDUCTIBLE)
        if tax_case_code != "IN_BEWIRTUNG":
            warnings.append("Bewirtung: Steuerfall „Bewirtung“ verwenden (70 % abziehbar, Pflichtangaben auf dem Beleg).")
    else:
        deductible = expense

    if category == "hardware_gwg" and abs(net) > Decimal("800.00"):
        warnings.append("Über 800 € netto ist Hardware kein GWG – als Anlagegut abschreiben (Steuerberater).")

    return ExpenseAmounts(net=net, tax=tax_on_document, gross=gross, rc_tax=rc_tax, input_tax=input_tax,
                          expense=expense, deductible=deductible, warnings=tuple(warnings))
