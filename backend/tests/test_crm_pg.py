"""Vertriebs-Pipeline: Lead → Kunde → Angebot → angenommen = gewonnen; Kennzahlen."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal


async def test_pipeline_lead_to_won(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    lid = (await api.post("/api/crm/leads", json={"title": "HR-Software Rollout", "company": "Rheinwerk AG", "contact_name": "Petra Weber",
                                                   "email": "p.weber@rheinwerk.de", "value": "12000.00", "source": "Messe"})).json()["id"]
    other = (await api.post("/api/crm/leads", json={"title": "Kleinauftrag", "value": "800"})).json()["id"]
    b = (await api.get("/api/crm")).json()
    assert b["stats"]["open"] == 2 and D(b["stats"]["pipeline"]) == D("12800.00") and D(b["stats"]["weighted"]) == D("1280.00")

    assert (await api.post(f"/api/crm/leads/{lid}/activities", json={"kind": "call", "text": "Erstgespräch, Bedarf 40 Nutzer"})).status_code == 201
    assert (await api.get(f"/api/crm/leads/{lid}")).json()["stage"] == "contacted"
    assert (await api.post(f"/api/crm/leads/{lid}/offer")).status_code == 409, "erst Kunde anlegen"
    c = await api.post(f"/api/crm/leads/{lid}/customer", json={"street": "Rheinallee 1", "postal_code": "40549", "city": "Düsseldorf"})
    assert c.status_code == 200, c.text
    oid = (await api.post(f"/api/crm/leads/{lid}/offer")).json()["offer_id"]
    lead = (await api.get(f"/api/crm/leads/{lid}")).json()
    assert lead["stage"] == "offer" and lead["offer_id"] == oid and lead["customer_id"]
    offer = (await api.get(f"/api/offers/{oid}")).json()
    assert D(offer["total_net"]) == D("12000.00")
    assert (await api.post(f"/api/offers/{oid}/send")).status_code in (200, 204)
    r = await api.post(f"/api/offers/{oid}/decision", json={"accept": True, "name": "Petra Weber"})
    assert r.status_code in (200, 204), r.text
    lead = (await api.get(f"/api/crm/leads/{lid}")).json()
    assert lead["stage"] == "won" and any("gewonnen" in a["text"] for a in lead["activities"])

    assert (await api.post(f"/api/crm/leads/{other}/move", json={"stage": "lost"})).status_code == 409, "Grund Pflicht"
    assert (await api.post(f"/api/crm/leads/{other}/move", json={"stage": "lost", "lost_reason": "zu teuer"})).status_code == 204
    s = (await api.get("/api/crm")).json()["stats"]
    assert s["won_year"] == 1 and s["lost_year"] == 1 and s["win_rate"] == 50 and D(s["won_value_year"]) == D("12000.00")
    assert (await api.delete(f"/api/crm/leads/{lid}")).status_code == 409
