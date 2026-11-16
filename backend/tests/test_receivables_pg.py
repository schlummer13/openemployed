"""Zahlungen und Mahnwesen gegen echtes PostgreSQL (Datum wird für die Routen vorgespult)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api import receivable_routes
from app.services.invoices import today_berlin
from app.services.receivables import half_year_start
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

TODAY = today_berlin()


def at(monkeypatch: pytest.MonkeyPatch, day: dt.date) -> None:
    monkeypatch.setattr(receivable_routes, "today_berlin", lambda: day)


async def issued(e: Env, customer: str, price: str = "1000.00") -> dict:
    inv = (await e.api.post("/api/invoices", json=draft(customer, price))).json()
    return (await e.api.post(f"/api/invoices/{inv['id']}/issue")).json()


async def ensure_rates(e: Env, *days: dt.date) -> None:
    for d in days:
        assert (await e.api.post("/api/base-rates", json={"valid_from": half_year_start(d).isoformat(), "rate": "1.27"})).status_code == 204


async def test_payments_partial_full_reverse(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    b2b, _ = await setup_basics(env)
    inv = await issued(env, b2b)
    gross = Decimal(inv["total_gross"])
    url = f"/api/invoices/{inv['id']}"

    r = (await env.api.get(f"{url}/receivable")).json()
    assert r["status"] == "open" and Decimal(r["total_open"]) == gross

    assert (await env.api.post(f"{url}/payments", json={"paid_on": TODAY.isoformat(), "amount": 100.0})).status_code == 422
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    assert (await env.api.post(f"{url}/payments", json={"paid_on": tomorrow, "amount": "1"})).status_code == 422
    assert (await env.api.post(f"{url}/payments", json={"paid_on": TODAY.isoformat(), "amount": str(gross + 1)})).status_code == 422

    p = await env.api.post(f"{url}/payments", json={"paid_on": TODAY.isoformat(), "amount": "500.00", "reference": "Bank 1"})
    assert p.status_code == 201
    r = (await env.api.get(f"{url}/receivable")).json()
    assert r["status"] == "partial" and Decimal(r["principal_open"]) == gross - 500
    assert [x.record_type for x in env.ledger.records].count("payment.received") == 1

    # Zahlungen sind unveränderlich
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE payments SET amount = 1"))
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("DELETE FROM payments"))

    await env.api.post(f"{url}/payments", json={"paid_on": TODAY.isoformat(), "amount": str(gross - 500)})
    r = (await env.api.get(f"{url}/receivable")).json()
    assert r["status"] == "paid" and Decimal(r["total_open"]) == 0
    assert all(i["invoice_id"] != inv["id"] for i in (await env.api.get("/api/receivables")).json()["items"])

    rev = await env.api.post(f"/api/payments/{r['payments'][1]['id']}/reverse", json={"reason": "Rücklastschrift"})
    assert rev.status_code == 204
    assert (await env.api.post(f"/api/payments/{r['payments'][1]['id']}/reverse", json={"reason": "nochmal"})).status_code == 409
    r = (await env.api.get(f"{url}/receivable")).json()
    assert r["status"] == "partial" and "payment.reversed" in [x.record_type for x in env.ledger.records]

    # Stornierte Rechnung nimmt keine Zahlungen an
    await env.api.post(f"{url}/cancel", json={"reason": "Fehler"})
    assert (await env.api.post(f"{url}/payments", json={"paid_on": TODAY.isoformat(), "amount": "1"})).status_code == 409


async def test_dunning_b2b_levels_interest_fee(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    b2b, _ = await setup_basics(env)
    inv = await issued(env, b2b)
    due = dt.date.fromisoformat(inv["due_date"])
    url = f"/api/invoices/{inv['id']}"
    mailer = env.api._transport.app.state.mailer  # type: ignore[attr-defined]

    at(monkeypatch, due + dt.timedelta(days=3))
    assert (await env.api.post(f"{url}/dunning")).status_code == 409, "Stufe 1 erst 7 Tage nach Fälligkeit"
    run0 = (await env.api.post("/api/dunning/run")).json()
    assert run0["created"] == []

    d1 = due + dt.timedelta(days=8)
    at(monkeypatch, d1)
    run1 = (await env.api.post("/api/dunning/run")).json()
    assert [c["level"] for c in run1["created"]] == [1] and len(run1["sent"]) == 1, run1
    assert mailer.sent[-1].subject.startswith("Zahlungserinnerung") and len(mailer.sent[-1].attachments) == 2
    assert (await env.api.post("/api/dunning/run")).json()["created"] == [], "idempotent"

    d2 = due + dt.timedelta(days=22)
    at(monkeypatch, d2)
    rates = (await env.api.get("/api/dunning-settings")).json()
    if rates["rate_missing"]:
        r = await env.api.post(f"{url}/dunning")
        assert r.status_code == 409 and "Basiszinssatz" in r.json()["detail"], "kein stilles Fortschreiben des Basiszinssatzes"
    await ensure_rates(env, d1, d2)
    notice = (await env.api.post(f"{url}/dunning")).json()
    assert notice["level"] == 2
    r = (await env.api.get(f"{url}/receivable")).json()
    assert r["default_from"] == (d1 + dt.timedelta(days=1)).isoformat()
    n2 = r["notices"][1]
    days = (d2 - d1).days  # Zinstage: Tag nach Zugang der Erinnerung bis einschließlich heute
    expected_interest = (Decimal("1190.00") * Decimal("10.27") / 100 * days / 365).quantize(Decimal("0.01"))
    assert Decimal(n2["interest"]) == expected_interest and Decimal(n2["fee"]) == Decimal("40.00")
    assert Decimal(n2["total"]) == Decimal("1190.00") + expected_interest + 40
    pdf = await env.api.get(f"/api/dunning/{n2['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # Mahnschreiben sind unveränderlich, Stufe 3 erst nach Versand von Stufe 2
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE dunning_notices SET total = 1"))
    at(monkeypatch, due + dt.timedelta(days=40))
    assert (await env.api.post(f"{url}/dunning")).status_code == 409
    assert (await env.api.post(f"/api/dunning/{n2['id']}/mark-sent", json={"channel": "post"})).status_code == 204

    # Mahnsperre
    assert (await env.api.put(f"{url}/dunning-block", json={"reason": "Reklamation offen"})).status_code == 204
    assert (await env.api.post("/api/dunning/run")).json()["created"] == []
    assert (await env.api.put(f"{url}/dunning-block", json={})).status_code == 204
    await ensure_rates(env, due + dt.timedelta(days=40))
    run3 = (await env.api.post("/api/dunning/run")).json()
    assert [c["level"] for c in run3["created"]] == [3]
    r = (await env.api.get(f"{url}/receivable")).json()
    assert Decimal(r["notices"][2]["fee"]) == 0, "Pauschale nur einmal"

    # Zahlung des Rechnungsbetrags: Kosten und Zinsen werden zuerst getilgt (§ 367) → Rest bleibt offen
    paid_on = due + dt.timedelta(days=40)
    assert (await env.api.post(f"{url}/payments", json={"paid_on": paid_on.isoformat(), "amount": "1190.00"})).status_code == 201
    r = (await env.api.get(f"{url}/receivable")).json()
    assert r["status"] == "overdue" and Decimal(r["costs_open"]) == 0 and Decimal(r["interest_open"]) <= Decimal("0.02")
    assert Decimal("40") < Decimal(r["principal_open"]) < Decimal("60"), "Rest = Pauschale + Zinsen auf Hauptforderung"


async def test_b2c_without_notice_no_default_until_reminder(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _, b2c = await setup_basics(env)
    inv = await issued(env, b2c, "119.00")
    due = dt.date.fromisoformat(inv["due_date"])
    r = (await env.api.get(f"/api/invoices/{inv['id']}/receivable")).json()
    # Hinweis auf der Rechnung vorhanden → Verzug 30 Tage nach Fälligkeit
    assert r["default_from"] == (due + dt.timedelta(days=31)).isoformat()
    settings = (await env.api.get("/api/dunning-settings")).json()["settings"]
    assert Decimal(settings["b2c_fee"]) == Decimal("2.50")
    saved = await env.api.put("/api/dunning-settings", json={**settings, "b2c_fee": "1.50", "auto_send": False})
    assert saved.status_code == 200 and saved.json()["auto_send"] is False
    at(monkeypatch, due + dt.timedelta(days=8))
    run = (await env.api.post("/api/dunning/run")).json()
    assert len(run["created"]) == 1 and run["sent"] == [], "ohne Auto-Versand nur Entwurf"
