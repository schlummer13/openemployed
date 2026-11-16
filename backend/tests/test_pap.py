"""Lohnsteuer nach amtlichem PAP: Abgleich mit Ergebnissen der externen BMF-Prüfschnittstelle (gespeichert, offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.pap.lst2026 import Lohnsteuer2026

CASES = json.loads((Path(__file__).parent / "data" / "pap2026_bmf.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[f"STKL{c['input']['STKL']}-LZZ{c['input']['LZZ']}-{c['input']['RE4']}" for c in CASES])
def test_pap_matches_bmf(case: dict) -> None:  # type: ignore[type-arg]
    r = Lohnsteuer2026(**case["input"]).calculate()
    assert {k: int(getattr(r, k)) for k in case["expected"]} == case["expected"]


def test_cases_cover_all_classes() -> None:
    assert {c["input"]["STKL"] for c in CASES} == {1, 2, 3, 4, 5, 6} and len(CASES) >= 60
    assert any(c["input"].get("SONSTB") for c in CASES) and any(c["input"].get("PKV") for c in CASES)
