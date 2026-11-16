"""Cron, Platzhalter und Bedingungen der Workflows."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain.workflows import WorkflowError, condition, cron_matches, parse_cron, render


def test_cron() -> None:
    mon8 = dt.datetime(2026, 11, 16, 8, 0)  # Montag
    assert cron_matches("0 8 * * 1", mon8) and not cron_matches("0 8 * * 2", mon8)
    assert cron_matches("*/15 * * * *", mon8.replace(minute=45)) and not cron_matches("*/15 * * * *", mon8.replace(minute=46))
    assert cron_matches("0 9 1 * *", dt.datetime(2026, 12, 1, 9, 0))
    assert cron_matches("0 8 1 * 1", mon8), "Tag ODER Wochentag"
    assert cron_matches("0 8 * * 7", dt.datetime(2026, 11, 15, 8, 0)), "7 = Sonntag"
    for bad in ("* * *", "61 * * * *", "* * * * mon", "*/0 * * * *"):
        with pytest.raises(WorkflowError):
            parse_cron(bad)


def test_render_and_condition() -> None:
    ctx = {"data": {"number": "RE-1", "total": "1190.00", "lines": [{"d": "x"}]}, "steps": {"a": {"result": {"ok": True}}}}
    assert render("Rechnung {{data.number}} über {{ data.total }} €", ctx) == "Rechnung RE-1 über 1190.00 €"
    assert render("{{steps.a.result.ok}}", ctx) is True and render("{{data.lines.0.d}}", ctx) == "x"
    assert render({"x": ["{{data.unbekannt}}"]}, ctx) == {"x": [None]}
    assert render("{{__class__}}", ctx) is None, "keine Attributzugriffe"
    assert condition("1190.00", ">", "1000") and not condition("999", ">", "1000") and condition("10,5", "==", "10.5")
    assert condition("Hosting AG", "contains", "hosting") and condition(None, "empty", None) and condition("b", ">", "a")
