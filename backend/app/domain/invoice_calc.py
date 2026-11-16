"""Rechnungsberechnung nach EN 16931.

Regeln (docs/konzept/04 §4.4 und 15 §4):
- Positionsnetto = Menge × Nettopreis, kaufmännisch auf Cent gerundet (BT-131)
- Nettopreis einer Position darf nicht negativ sein (BR-27); Gutschriftspositionen über negative Menge
- Steuer je (Kategorie, Satz) = Summe der Positionsnettobeträge × Satz, auf Cent gerundet (BR-CO-17)
- Gesamtsummen: Netto = Σ Bemessungsgrundlagen, Steuer = Σ Steuerbeträge, Brutto = Netto + Steuer

Bruttopreise (B2C, Preisangabenverordnung):
- Je Steuergruppe wird der Bruttobetrag G der Positionen gebildet.
- Gesucht ist der größte Nettobetrag N (Cent), für den N + round(N × Satz) ≤ G gilt.
  Nicht jeder Bruttobetrag ist exakt erreichbar (z. B. 1,10 € bei 19 %); dann ist die Rechnung um
  1 Cent günstiger als der angezeigte Preis – niemals teurer – und es wird eine Warnung ausgegeben.
- N wird so auf die Positionen verteilt, dass die Summe exakt stimmt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.core.money import CENT, PRICE_QUANTUM, ZERO, decimal_places, round_cent
from app.domain.tax_cases import Direction, TaxCase, VatCategory, get_tax_case

MAX_LINES = 500

# UN/ECE Recommendation 20 (Auswahl)
UNIT_CODES: dict[str, str] = {
    "C62": "Stück",
    "HUR": "Stunde",
    "MIN": "Minute",
    "DAY": "Tag",
    "WEE": "Woche",
    "MON": "Monat",
    "ANN": "Jahr",
    "LS": "Pauschale",
    "KMT": "Kilometer",
}


class PriceMode(StrEnum):
    NET = "net"
    GROSS = "gross"


class InvoiceCalculationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LineInput:
    description: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal  # netto oder brutto je nach PriceMode
    tax_case: str


@dataclass(frozen=True, slots=True)
class LineResult:
    position: int
    description: str
    quantity: Decimal
    unit_code: str
    unit_price_net: Decimal
    line_net: Decimal
    tax_case: str
    category: VatCategory
    rate: Decimal
    unit_price_gross: Decimal | None = None
    line_gross: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TaxBreakdown:
    category: VatCategory
    rate: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    tax_cases: tuple[str, ...]
    exemption_reason: str | None
    vatex: str | None


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    price_mode: PriceMode
    lines: tuple[LineResult, ...]
    tax_breakdown: tuple[TaxBreakdown, ...]
    total_net: Decimal
    total_tax: Decimal
    total_gross: Decimal
    gross_target: Decimal | None = None   # Summe der angezeigten Bruttopreise (nur Bruttomodus)
    gross_deviation: Decimal = ZERO       # gross_target − total_gross (0,00 oder 0,01 je Gruppe)
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _validate_line(index: int, line: LineInput) -> TaxCase:
    pos = f"Position {index}"
    if not line.description.strip():
        raise InvoiceCalculationError(f"{pos}: Bezeichnung fehlt")
    if len(line.description) > 1000:
        raise InvoiceCalculationError(f"{pos}: Bezeichnung zu lang (max. 1000 Zeichen)")
    if line.unit_code not in UNIT_CODES:
        raise InvoiceCalculationError(f"{pos}: unbekannte Einheit {line.unit_code!r}")
    for name, value in (("Menge", line.quantity), ("Preis", line.unit_price)):
        if not value.is_finite() or decimal_places(value) > 4:
            raise InvoiceCalculationError(f"{pos}: {name} mit höchstens 4 Nachkommastellen angeben")
    if line.unit_price < 0:
        raise InvoiceCalculationError(f"{pos}: Preis darf nicht negativ sein (Minderungen über negative Menge)")
    case = get_tax_case(line.tax_case)
    if case.direction is not Direction.OUTGOING:
        raise InvoiceCalculationError(f"{pos}: Steuerfall {case.code} ist kein Ausgangs-Steuerfall")
    if not case.active:
        raise InvoiceCalculationError(f"{pos}: Steuerfall {case.code} ist nicht freigeschaltet")
    return case


def _de(value: Decimal) -> str:
    """Deutsche Schreibweise für Hinweistexte: 2093.12 → 2.093,12"""
    text = f"{value:,.2f}" if value != value.to_integral_value() or "." in str(value) else f"{value:,}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def tax_for(taxable: Decimal, rate: Decimal) -> Decimal:
    """Steuerbetrag einer Gruppe (BR-CO-17)."""
    return round_cent(taxable * rate / Decimal(100))


def net_from_gross(gross: Decimal, rate: Decimal, *, round_up: bool = False) -> tuple[Decimal, Decimal]:
    """Nettobetrag zu einem Bruttobetrag, gerechnet auf dem Betrag (Vorzeichen bleibt erhalten).

    ``round_up=False``: größter Betrag N mit |N| + Steuer ≤ |Brutto| (Standard).
    ``round_up=True``:  kleinster Betrag N mit |N| + Steuer ≥ |Brutto|.
    Gibt (netto, steuer) zurück; ``net_from_gross(-g) == (-n, -s)`` (Storno ist exaktes Spiegelbild).
    """
    gross = round_cent(gross)
    if rate == 0:
        return gross, ZERO
    sign = Decimal(-1) if gross < 0 else Decimal(1)
    target = abs(gross)
    estimate = round_cent(target / (1 + rate / Decimal(100)))
    best: Decimal | None = None
    # f(n) = n + round(n·r) ist monoton steigend; das Optimum liegt höchstens wenige Cent neben der Schätzung
    for step in range(-3, 4):
        candidate = estimate + step * CENT
        if candidate < 0:
            continue
        total = candidate + tax_for(candidate, rate)
        if round_up:
            if total >= target and (best is None or candidate < best):
                best = candidate
        elif total <= target and (best is None or candidate > best):
            best = candidate
    if best is None:  # pragma: no cover – mathematisch ausgeschlossen
        raise InvoiceCalculationError("Bruttobetrag nicht berechenbar")
    net = best * sign
    return net, tax_for(net, rate)


def is_gross_price_representable(gross: Decimal, rate: Decimal) -> bool:
    """True, wenn der Bruttobetrag bei diesem Steuersatz exakt erreichbar ist (für Produktpflege)."""
    net, tax = net_from_gross(gross, rate)
    return net + tax == round_cent(gross)


def _unit_price_for(line_net: Decimal, quantity: Decimal) -> Decimal:
    """Nettopreis (4 Stellen), für den round(Menge × Preis) exakt das Positionsnetto ergibt."""
    if quantity == 0:
        return ZERO
    base = (line_net / quantity).quantize(PRICE_QUANTUM)
    for step in (0, 1, -1, 2, -2, 3, -3):
        candidate = base + step * PRICE_QUANTUM
        if candidate >= 0 and round_cent(quantity * candidate) == line_net:
            return candidate
    return max(base, ZERO)


def calculate_invoice(lines: Sequence[LineInput], price_mode: PriceMode) -> InvoiceTotals:
    if not lines:
        raise InvoiceCalculationError("Mindestens eine Position erforderlich")
    if len(lines) > MAX_LINES:
        raise InvoiceCalculationError(f"Höchstens {MAX_LINES} Positionen")

    cases = [_validate_line(i, line) for i, line in enumerate(lines, start=1)]
    groups: dict[tuple[VatCategory, Decimal], list[int]] = {}
    for idx, case in enumerate(cases):
        groups.setdefault((case.category, case.rate), []).append(idx)

    # Bruttomodus: Ausrichtung der Rechnung (Rechnung > 0, Storno/Gutschrift < 0).
    # Gruppen mit gleichem Vorzeichen wie die Rechnung werden abgerundet, Gruppen mit entgegengesetztem
    # Vorzeichen (z. B. Rabatt auf 7 %-Positionen) aufgerundet → der Kunde zahlt nie mehr als angezeigt,
    # und ein Storno ist exakt das Spiegelbild der Rechnung.
    gross_by_line = {
        i: round_cent(line.quantity * line.unit_price) for i, line in enumerate(lines)
    } if price_mode is PriceMode.GROSS else {}
    orientation = -1 if sum(gross_by_line.values(), ZERO) < 0 else 1

    line_net: list[Decimal] = [ZERO] * len(lines)
    unit_price_net: list[Decimal] = [ZERO] * len(lines)
    line_gross: list[Decimal | None] = [None] * len(lines)
    warnings: list[str] = []
    breakdown: list[TaxBreakdown] = []
    gross_target_total = ZERO

    for (category, rate), indices in sorted(groups.items(), key=lambda kv: (kv[0][0].value, -kv[0][1])):
        if price_mode is PriceMode.NET:
            for i in indices:
                line_net[i] = round_cent(lines[i].quantity * lines[i].unit_price)
                unit_price_net[i] = lines[i].unit_price
            taxable = sum((line_net[i] for i in indices), ZERO)
            tax = tax_for(taxable, rate)
        else:
            grosses = {i: gross_by_line[i] for i in indices}
            for i, g in grosses.items():
                line_gross[i] = g
            group_gross = sum(grosses.values(), ZERO)
            gross_target_total += group_gross
            against_orientation = group_gross * orientation < 0
            taxable, tax = net_from_gross(group_gross, rate, round_up=against_orientation)
            divisor = 1 + rate / Decimal(100)
            for i in indices:
                line_net[i] = round_cent(grosses[i] / divisor)
            residual = taxable - sum((line_net[i] for i in indices), ZERO)
            if residual:
                # Rest auf die betragsmäßig größte Position (deterministisch: erste bei Gleichstand)
                anchor = max(indices, key=lambda i: (abs(grosses[i]), -i))
                line_net[anchor] += residual
            for i in indices:
                unit_price_net[i] = _unit_price_for(line_net[i], lines[i].quantity)
            deviation = group_gross - (taxable + tax)
            if deviation:
                effect = "zugunsten des Kunden" if orientation > 0 else "spiegelbildlich zur Ursprungsrechnung"
                warnings.append(
                    f"Bruttobetrag {_de(group_gross)} € ist bei {_de(rate)} % nicht exakt darstellbar; "
                    f"berechnet werden {_de(taxable + tax)} € (Abweichung {_de(deviation)} €, {effect})."
                )

        member_cases = tuple(dict.fromkeys(cases[i].code for i in indices))
        first = cases[indices[0]]
        breakdown.append(
            TaxBreakdown(
                category=category,
                rate=rate,
                taxable_amount=taxable,
                tax_amount=tax,
                tax_cases=member_cases,
                exemption_reason=first.invoice_note,
                vatex=first.vatex,
            )
        )

    results = tuple(
        LineResult(
            position=i + 1,
            description=line.description.strip(),
            quantity=line.quantity,
            unit_code=line.unit_code,
            unit_price_net=unit_price_net[i],
            line_net=line_net[i],
            tax_case=cases[i].code,
            category=cases[i].category,
            rate=cases[i].rate,
            unit_price_gross=line.unit_price if price_mode is PriceMode.GROSS else None,
            line_gross=line_gross[i],
        )
        for i, line in enumerate(lines)
    )
    total_net = sum((b.taxable_amount for b in breakdown), ZERO)
    total_tax = sum((b.tax_amount for b in breakdown), ZERO)
    total_gross = total_net + total_tax

    # Invarianten – schlagen sie fehl, ist das ein Programmierfehler, kein Eingabefehler.
    # Bewusst kein `assert` (würde mit python -O entfernt).
    if sum((r.line_net for r in results), ZERO) != total_net:
        raise RuntimeError("Invariante verletzt: Summe der Positionen ≠ Netto")
    if any(b.tax_amount != tax_for(b.taxable_amount, b.rate) for b in breakdown):
        raise RuntimeError("Invariante verletzt: BR-CO-17")

    return InvoiceTotals(
        price_mode=price_mode,
        lines=results,
        tax_breakdown=tuple(breakdown),
        total_net=total_net,
        total_tax=total_tax,
        total_gross=total_gross,
        gross_target=gross_target_total if price_mode is PriceMode.GROSS else None,
        gross_deviation=(gross_target_total - total_gross) if price_mode is PriceMode.GROSS else ZERO,
        warnings=tuple(warnings),
    )
