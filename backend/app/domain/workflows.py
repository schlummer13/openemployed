"""Bausteine für eigene Workflows: Cron-Ausdrücke, Platzhalter ``{{pfad}}`` und Bedingungen – bewusst ohne Template-Engine
(kein Code in Vorlagen, keine Server-Side-Template-Injection)."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")
OPS = ("==", "!=", ">", ">=", "<", "<=", "contains", "not_contains", "empty", "not_empty")


class WorkflowError(ValueError):
    pass


# ───────── Cron (Minute Stunde Tag Monat Wochentag; Wochentag 0/7 = Sonntag) ─────────

def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise WorkflowError("Schrittweite muss ≥ 1 sein")
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise WorkflowError(f"Wert außerhalb {lo}–{hi}: {part}")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    parts = expr.split()
    if len(parts) != 5:
        raise WorkflowError("Cron-Ausdruck braucht 5 Felder: Minute Stunde Tag Monat Wochentag")
    try:
        minute, hour, dom, month = _field(parts[0], 0, 59), _field(parts[1], 0, 23), _field(parts[2], 1, 31), _field(parts[3], 1, 12)
        dow = {d % 7 for d in _field(parts[4], 0, 7)}
    except ValueError as exc:
        raise WorkflowError(f"Cron-Ausdruck ungültig: {exc}") from exc
    return minute, hour, dom, month, dow, parts[2] == "*", parts[4] == "*"


def cron_matches(expr: str, at: dt.datetime) -> bool:
    minute, hour, dom, month, dow, dom_any, dow_any = parse_cron(expr)
    if at.minute not in minute or at.hour not in hour or at.month not in month:
        return False
    day_ok, wd_ok = at.day in dom, (at.isoweekday() % 7) in dow
    if dom_any or dow_any:  # klassische Cron-Logik: beide eingeschränkt = ODER
        return day_ok and wd_ok
    return day_ok or wd_ok


def describe_cron(expr: str) -> str:
    presets = {"0 7 * * *": "täglich um 07:00", "0 8 * * 1": "montags um 08:00", "0 9 1 * *": "am 1. jedes Monats um 09:00",
               "*/15 * * * *": "alle 15 Minuten", "0 * * * *": "stündlich"}
    return presets.get(expr.strip(), expr)


# ───────── Platzhalter und Werte ─────────

def lookup(ctx: dict[str, Any], path: str) -> Any:
    cur: Any = ctx
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
            cur = cur[int(key)]
        else:
            return None
    return cur


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Ersetzt ``{{pfad}}`` in Texten (rekursiv in Listen/Objekten). Ein Text, der nur aus einem Platzhalter besteht, behält den Typ."""
    if isinstance(value, str):
        m = PLACEHOLDER.fullmatch(value.strip())
        if m:
            return lookup(ctx, m.group(1))
        return PLACEHOLDER.sub(lambda mm: "" if (v := lookup(ctx, mm.group(1))) is None else str(v), value)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v).replace(",", ".")) if v is not None and str(v).strip() != "" else None
    except InvalidOperation:
        return None


def condition(left: Any, op: str, right: Any) -> bool:
    if op not in OPS:
        raise WorkflowError(f"Unbekannter Vergleich {op}")
    if op == "empty":
        return left in (None, "", [], {})
    if op == "not_empty":
        return left not in (None, "", [], {})
    if op in ("contains", "not_contains"):
        hit = str(right).lower() in (str(left).lower() if not isinstance(left, list) else [str(x).lower() for x in left])
        return hit if op == "contains" else not hit
    a, b = _num(left), _num(right)
    if a is not None and b is not None:
        pair: tuple[Any, Any] = (a, b)
    else:
        pair = (str(left if left is not None else ""), str(right if right is not None else ""))
    x, y = pair
    return bool({"==": x == y, "!=": x != y, ">": x > y, ">=": x >= y, "<": x < y, "<=": x <= y}[op])
