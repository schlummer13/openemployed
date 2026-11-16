"""Forderungsstand, Verzugszinsen und Mahnstufen (BGB §§ 286, 288, 367).

- Verzugszinsen: B2B 9, B2C 5 Prozentpunkte über dem Basiszinssatz (§ 288 Abs. 1/2 BGB), tagesgenau (act/365),
  Basiszinssatz je Halbjahr (§ 247 BGB) – Wechsel innerhalb des Zinszeitraums werden aufgeteilt.
- Verzugspauschale 40 € nur gegenüber Unternehmern (§ 288 Abs. 5 BGB).
- Verrechnung von Teilzahlungen: Kosten → Zinsen → Hauptforderung (§ 367 Abs. 1 BGB).
- Zinsen und Kosten sind nicht steuerbarer Schadensersatz (keine USt).

Alle Beträge sind ``Decimal``; Zinsen werden je Abschnitt exakt aufsummiert und erst am Ende kaufmännisch gerundet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
CENT = D("0.01")
B2B_MARGIN = D("9")
B2C_MARGIN = D("5")
B2B_FLAT_FEE = D("40.00")

# Stufe → Tage nach Fälligkeit (Gründervorgabe, doc 14 §3.4); Mindestabstand zwischen zwei Schreiben
LEVEL_DAYS = {1: 7, 2: 21, 3: 35}
MIN_GAP_DAYS = 7
LEVEL_TITLES = {1: "Zahlungserinnerung", 2: "Mahnung", 3: "Letzte Mahnung"}


def q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BaseRate:
    valid_from: dt.date
    rate: Decimal  # Prozent, kann negativ sein


@dataclass(frozen=True)
class Payment:
    paid_on: dt.date
    amount: Decimal  # > 0


@dataclass(frozen=True)
class Cost:
    """Mahnkosten (Pauschale/Gebühr), fällig ab ``on``."""

    on: dt.date
    amount: Decimal


@dataclass
class ClaimState:
    principal_open: Decimal
    interest_open: Decimal
    costs_open: Decimal
    interest_total: Decimal  # angefallen (inkl. bereits bezahlter)
    paid_total: Decimal
    rate_segments: list[tuple[dt.date, dt.date, Decimal]] = field(default_factory=list)

    @property
    def total_open(self) -> Decimal:
        return self.principal_open + self.interest_open + self.costs_open

    @property
    def settled(self) -> bool:
        return self.total_open <= 0


def rate_on(rates: list[BaseRate], day: dt.date) -> Decimal:
    applicable = [r for r in rates if r.valid_from <= day]
    if not applicable:
        raise ValueError(f"Kein Basiszinssatz für {day:%d.%m.%Y} hinterlegt")
    return max(applicable, key=lambda r: r.valid_from).rate


def _interest(principal: Decimal, start: dt.date, end: dt.date, rates: list[BaseRate], margin: Decimal,
              segments: list[tuple[dt.date, dt.date, Decimal]]) -> Decimal:
    """Zinsen für die Tage [start, end) – exakt, ungerundet. Negativer Gesamtsatz → 0 (keine Negativzinsen)."""
    if principal <= 0 or end <= start:
        return D(0)
    total = D(0)
    cursor = start
    changes = sorted({r.valid_from for r in rates if start < r.valid_from < end})
    for boundary in [*changes, end]:
        pct = max(D(0), rate_on(rates, cursor) + margin)
        days = (boundary - cursor).days
        total += principal * pct / 100 * days / 365
        segments.append((cursor, boundary, pct))
        cursor = boundary
    return total


def claim_state(*, principal: Decimal, default_from: dt.date | None, as_of: dt.date, payments: list[Payment],
                costs: list[Cost], rates: list[BaseRate], b2b: bool) -> ClaimState:
    """Forderungsstand am Ende von ``as_of``.

    ``default_from`` ist der erste Verzugstag (Zinsen laufen ab diesem Tag, § 288 i. V. m. § 187 BGB analog).
    Zahlungen mindern ab ihrem Eingangstag.
    """
    margin = B2B_MARGIN if b2b else B2C_MARGIN
    principal_open = principal
    interest_open = D(0)
    costs_open = D(0)
    interest_total = D(0)
    paid_total = D(0)
    segments: list[tuple[dt.date, dt.date, Decimal]] = []
    accrued_until = default_from

    events: list[tuple[dt.date, int, Decimal]] = [(c.on, 0, c.amount) for c in costs if c.on <= as_of]
    events += [(p.paid_on, 1, p.amount) for p in payments if p.paid_on <= as_of]
    events.sort(key=lambda e: (e[0], e[1]))

    def accrue(until: dt.date) -> None:
        nonlocal interest_open, interest_total, accrued_until
        if accrued_until is None or until <= accrued_until:
            return
        amount = _interest(principal_open, accrued_until, until, rates, margin, segments)
        interest_open += amount
        interest_total += amount
        accrued_until = until

    for day, kind, amount in events:
        accrue(day)
        if kind == 0:
            costs_open += amount
            continue
        paid_total += amount
        rest = amount
        for bucket in ("costs", "interest", "principal"):
            if bucket == "costs":
                take = min(rest, costs_open)
                costs_open -= take
            elif bucket == "interest":
                take = min(rest, q(interest_open)) if interest_open > 0 else D(0)
                interest_open -= take
            else:
                take = min(rest, principal_open)
                principal_open -= take
            rest -= take
        principal_open -= rest  # Überzahlung → negativer Saldo (Guthaben des Kunden)
    accrue(as_of + dt.timedelta(days=1))
    return ClaimState(principal_open=principal_open, interest_open=max(D(0), q(interest_open)), costs_open=costs_open,
                      interest_total=q(interest_total), paid_total=paid_total, rate_segments=segments)


def default_start(*, due_date: dt.date, b2b: bool, b2c_notice_on_invoice: bool,
                  first_reminder_received: dt.date | None) -> dt.date | None:
    """Erster Verzugstag.

    § 286 Abs. 3 BGB: 30 Tage nach Fälligkeit und Zugang (bei Verbrauchern nur mit Hinweis auf der Rechnung).
    § 286 Abs. 1 BGB: Mahnung nach Fälligkeit → Verzug ab Zugang der Mahnung (Zahlungserinnerung mit eindeutiger
    Zahlungsaufforderung genügt). Zinsbeginn ist der Folgetag des jeweiligen Ereignisses.
    """
    candidates: list[dt.date] = []
    if b2b or b2c_notice_on_invoice:
        candidates.append(due_date + dt.timedelta(days=31))
    if first_reminder_received is not None and first_reminder_received > due_date:
        candidates.append(first_reminder_received + dt.timedelta(days=1))
    return min(candidates) if candidates else None


def next_level(*, due_date: dt.date, today: dt.date, last_level: int, last_notice_on: dt.date | None) -> int | None:
    """Welche Stufe ist heute fällig? ``None`` = nichts zu tun."""
    if last_level >= 3:
        return None
    level = last_level + 1
    if (today - due_date).days < LEVEL_DAYS[level]:
        return None
    if last_notice_on is not None and (today - last_notice_on).days < MIN_GAP_DAYS:
        return None
    return level
