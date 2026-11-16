"""Willkommensmail, Gesellschafter-Stammdaten, Aufgabenzuordnung, Dashboard/Liquidität, neue Automatisierungen."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.api.team_routes import tax_id_check
from app.main import app
from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, Env, draft, env, setup_basics  # noqa: F401

TODAY = today_berlin()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x05" * 80


def test_tax_id_check_digit() -> None:
    assert tax_id_check("86095742719")
    assert not tax_id_check("86095742718")


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_welcome_mail(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    before = len(app.state.mailer.sent)
    c = (await api.post("/api/customers", json={**B2B, "company_name": "Neukunde GmbH", "email": "einkauf@neukunde.de"})).json()
    assert c["welcome"] == {"access": True, "mail_sent": True}
    mail = app.state.mailer.sent[before]
    assert mail.to == ["einkauf@neukunde.de"] and "Willkommen" in mail.subject and "/portal" in mail.text and c["number"] in mail.text
    access = (await api.get(f"/api/customers/{c['id']}/portal-access")).json()
    assert [a["email"] for a in access] == ["einkauf@neukunde.de"]
    # abschaltbar
    await api.put("/api/finance/settings", json={"min_liquidity": "1000", "monthly_fixed_costs": "0", "offer_followup_days": 7,
                                                 "offer_auto_reminder": False, "welcome_mail": False})
    c2 = (await api.post("/api/customers", json={**B2B, "company_name": "Ohne Mail GmbH", "email": "x@ohne.de"})).json()
    assert "welcome" not in c2
    app.state.mailer = None
    r = await api.post(f"/api/customers/{c2['id']}/welcome")
    assert r.status_code == 503


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_shareholder_profile_photo_and_tasks(env: Env) -> None:  # noqa: F811
    api = env.api
    bad = await api.post("/api/shareholders", json={"name": "Anna", "share_percent": "25", "tax_id": "86095742718"})
    assert bad.status_code == 422 and "Steuer-ID" in bad.text
    r = await api.post("/api/shareholders", json={
        "name": "Anna Beispiel", "email": "Anna@Beispiel.de", "share_percent": "25", "phone": "+49 211 123", "birth_date": "1990-04-01",
        "street": "Kö 1", "postal_code": "40212", "city": "Düsseldorf", "tax_id": "86 095 742 719",
        "social_security_number": "12 010490 B 123", "iban": "DE89 3704 0044 0532 0130 00", "role_title": "Geschäftsführerin Technik",
        "joined_on": "2026-12-01", "color": "#10b981"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert (await api.put(f"/api/shareholders/{sid}/photo", files={"file": ("a.gif", b"GIF89a", "image/gif")})).status_code == 409
    assert (await api.put(f"/api/shareholders/{sid}/photo", files={"file": ("a.png", PNG, "image/png")})).status_code == 204
    photo = await api.get(f"/api/shareholders/{sid}/photo")
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/png"
    team = (await api.get("/api/team")).json()["shareholders"][0]
    assert team["has_photo"] and team["tax_id"] == "86095742719" and team["iban"] == "DE89370400440532013000"
    assert team["social_security_number"] == "12010490B123"

    p = (await api.post("/api/projects", json={"name": "Website"})).json()
    board = (await api.get(f"/api/projects/{p['id']}/board")).json()
    col = board["columns"][0]["id"]
    await api.post(f"/api/projects/{p['id']}/tasks", json={"column_id": col, "title": "Impressum", "priority": "niedrig"})
    await api.post(f"/api/projects/{p['id']}/tasks", json={"column_id": col, "title": "Server härten", "priority": "dringend",
                                                           "assignee_id": sid})
    board = (await api.get(f"/api/projects/{p['id']}/board")).json()
    task = next(t for t in board["columns"][0]["tasks"] if t["title"] == "Server härten")
    assert task["assignee_name"] == "Anna Beispiel" and task["assignee_color"] == "#10b981" and task["assignee"] == "anna@beispiel.de"
    dash = (await api.get("/api/dashboard")).json()
    assert [t["title"] for t in dash["project_tasks"]][:2] == ["Server härten", "Impressum"], "nach Priorität sortiert"


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_capital_and_months(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    dash = (await api.get("/api/dashboard")).json()
    cap = dash["capital"]
    assert cap["balance"]["estimated"] is True and Decimal(cap["vat_due"]) == Decimal("334.02")
    assert any("Kontostand erfassen" in t["title"] for t in dash["tasks"])
    await api.post("/api/bank/balance", json={"balance_on": (TODAY - dt.timedelta(days=1)).isoformat(), "amount": "10000.00"})
    cap = (await api.get("/api/dashboard")).json()["capital"]
    assert cap["balance"]["estimated"] is False and Decimal(cap["balance"]["amount"]) == Decimal("10000.00")
    expected = Decimal("10000.00") - Decimal("334.02") - Decimal(cap["income_tax_reserve"]) - Decimal(cap["payables"]) \
        - Decimal(cap["salaries_30d"]) - Decimal(cap["fixed_costs_30d"])
    assert Decimal(cap["available"]) == expected
    months = (await api.get("/api/dashboard")).json()["months"]
    assert len(months) == 12 and Decimal(months[-1]["income"]) == Decimal("1758.00")

    await api.put("/api/finance/settings", json={"min_liquidity": "50000", "monthly_fixed_costs": "500", "offer_followup_days": 7,
                                                 "offer_auto_reminder": False, "welcome_mail": True})
    dash = (await api.get("/api/dashboard")).json()
    red = [t["title"] for t in dash["tasks"] if t["severity"] == "red"]
    assert dash["capital"]["below_minimum"] and "Liquidität unter Mindestreserve" in red
    before = len(app.state.mailer.sent)
    await api.post("/api/shareholders", json={"name": "Ben", "email": "ben@beispiel.de", "share_percent": "25"})
    r1 = (await api.post("/api/automations/liquidity/run")).json()
    assert r1["summary"].startswith("Warnung") and len(app.state.mailer.sent) == before + 1
    r2 = (await api.post("/api/automations/liquidity/run")).json()
    assert "bereits" in r2["summary"] and len(app.state.mailer.sent) == before + 1, "höchstens eine Warnung pro Woche"
    rep = (await api.post("/api/automations/monthly_report/run")).json()
    assert rep["status"] == "ok" and "Monatsbericht" in app.state.mailer.sent[-1].subject


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_offer_reminder(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    offer = (await api.post("/api/offers", json={"customer_id": b2b, "title": "Wartung", "valid_until": (TODAY + dt.timedelta(days=20)).isoformat(),
                                                 "lines": [{"description": "x", "quantity": "1", "unit_price": "100.00"}]})).json()
    await api.post(f"/api/offers/{offer['id']}/send")
    automations = {a["key"]: a for a in (await api.get("/api/automations")).json()}
    assert automations["offer_reminder"]["enabled"] is False, "Kundenmails nur nach bewusstem Einschalten"
    from app.api import automation_routes

    later = TODAY + dt.timedelta(days=10)  # Zeit vorspulen (versendete Angebote sind unveränderlich, auch für Tests)
    monkeypatch.setattr(automation_routes, "today_berlin", lambda: later)
    dash = (await api.get("/api/dashboard")).json()
    assert any("ohne Rückmeldung" in t["title"] for t in dash["tasks"])
    before = len(app.state.mailer.sent)
    r = (await api.post("/api/automations/offer_reminder/run")).json()
    assert r["summary"] == "1 Erinnerung(en) versendet" and "Erinnerung: Angebot" in app.state.mailer.sent[before].subject
    r = (await api.post("/api/automations/offer_reminder/run")).json()
    assert r["summary"] == "0 Erinnerung(en) versendet", "nur einmal je Angebot"
