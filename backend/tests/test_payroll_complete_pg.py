"""Lohnlauf mit allen Lohnarten: Einmalzahlung, Sachbezug (Freigrenze), Dienstwagen, Vorschuss, Pfändung, unbezahlter Urlaub,
DEÜV-Meldungen, Beitragsnachweis und Lohnsteuerbescheinigung."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.lohnsteuer import WageTaxInput, wage_tax
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal
PERIOD = f"{TODAY:%Y-%m}"
FIRST = TODAY.replace(day=1)

CARLA = {"first_name": "Carla", "last_name": "Komplett", "email": "carla@firma.de", "entry_date": f"{TODAY:%Y-%m}-01", "birth_date": "1988-04-02",
         "salary_type": "monthly", "gross_month": "4500.00", "weekly_hours": "40", "tax_class": 1, "state": "NW", "health_insurer": "AOK",
         "kv_additional_rate": "2.9", "children_under_25": 1, "social_security_number": "12020488K123", "tax_id": "12345678901",
         "iban": "DE89370400440532013000", "car_list_price": "48750.00", "car_distance_km": "12", "car_kind": "electric",
         "activity_key": "431124312"}


async def test_full_payroll(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    assert (await api.put("/api/payroll/settings", json={"employer_number": "12345678"})).status_code == 204
    emp = (await api.post("/api/payroll/employees", json=CARLA)).json()
    eid = emp["id"]
    # Positionen: Einmalzahlung, Gutschein (Freigrenze), Vorschuss
    for item in ({"kind": "one_time", "label": "Jahresprämie", "amount": "2000"},
                 {"kind": "benefit", "label": "Tankgutschein", "amount": "50", "voucher": True},
                 {"kind": "advance", "label": "Vorschuss vom 10.", "amount": "300"}):
        assert (await api.post("/api/payroll/items", json={"employee_id": eid, "period": PERIOD, **item})).status_code == 201
    assert (await api.post("/api/payroll/items", json={"employee_id": eid, "period": PERIOD, "kind": "benefit", "label": "noch einer",
                                                       "amount": "10", "voucher": True})).status_code == 409, "50-€-Freigrenze"
    items = (await api.get(f"/api/payroll/items?period={PERIOD}")).json()
    assert {i["kind"] for i in items} == {"one_time", "tax_free", "advance"}
    # Pfändung nach Tabelle (keine Unterhaltspflicht)
    assert (await api.post("/api/payroll/garnishments", json={"employee_id": eid, "creditor": "Inkasso GmbH", "reference": "M 12/26",
                                                              "creditor_iban": "DE02120300000000202051", "claim_total": "5000",
                                                              "served_on": FIRST.isoformat()})).status_code == 201
    run = (await api.post(f"/api/payroll/runs/{PERIOD}", json={})).json()
    s = next(p for p in run["payslips"] if p["name"] == "Carla Komplett")
    d = s["details"]
    # Dienstwagen E-Auto: 48.700 × 1 % × ¼ = 121,75 + 48.700 × 0,03 % × 12 km × ¼ = 43,83 → 165,58
    car = next(ln for ln in d["lines"] if ln.get("type") == "company_car")
    assert D(car["amount"]) == D("165.58")
    assert D(s["gross"]) == D("4500") + D("165.58") + D("2000")
    assert D(d["one_time"]) == D("2000") and D(d["benefits"]) == D("165.58") and D(d["tax_free"]) == D("50")
    # Lohnsteuer = PAP auf laufenden Lohn inkl. Sachbezug + sonstiger Bezug nach Jahreslohnverfahren
    expected = wage_tax(WageTaxInput(year=TODAY.year, monthly_wage=D("4665.58"), child_allowances=D(0), kv_additional_rate=D("2.9"),
                                     birth_year=1988, one_time=D("2000"), annual_regular=D("4665.58") * (13 - TODAY.month)))
    assert D(s["lst"]) == expected.lst + expected.lst_one_time
    garn = d["garnishments"][0]
    assert D(garn["amount"]) > 0 and garn["creditor"] == "Inkasso GmbH"
    assert D(s["payout"]) == D(s["net"]) - D("165.58") + D("50") - D("300") - D(garn["amount"])
    assert d["sv_days"] == 30 and d["contribution_group"] == "1111" and D(d["sv_gross_rv"]) > D("4500")

    # SEPA: Gehalt + Pfändungsbetrag an den Gläubiger
    approved = (await api.post(f"/api/payroll/runs/{PERIOD}/approve")).json()
    assert approved["status"] == "approved"
    from app.domain.sepa import next_business_day

    exe = next_business_day(TODAY, 1).isoformat()
    fid = (await api.post("/api/sepa/salaries", json={"period": PERIOD, "execution_date": exe})).json()["id"]
    xml = (await api.get(f"/api/sepa/files/{fid}.xml")).text
    assert "Inkasso GmbH" in xml and "M 12/26" in xml

    # Meldungen: Anmeldung (Grund 10) offen, Beitragsnachweis mit Fälligkeit
    reps = (await api.get("/api/payroll/sv-reports")).json()
    ann = next(r for r in reps["reports"] if r["reason"] == "10" and r["employee_id"] == eid)
    assert ann["data"]["employer_number"] == "12345678" and ann["data"]["person_group"] == "101" and ann["data"]["activity_key"] == "431124312"
    assert (await api.post(f"/api/payroll/sv-reports/{ann['id']}", json={"status": "submitted", "reference": "SV-4711"})).status_code == 204
    old = (await api.post("/api/payroll/employees", json={**CARLA, "email": "alt@firma.de", "first_name": "Alt", "entry_date": "2024-01-01",
                                                          "car_list_price": None})).json()
    reps = (await api.get("/api/payroll/sv-reports")).json()
    assert not any(r["employee_id"] == old["id"] and r["reason"] == "10" for r in reps["reports"]), "vor dem ersten Lohnlauf bereits gemeldet"
    st = (await api.get(f"/api/payroll/statements?period={PERIOD}")).json()
    aok = next(x for x in st if x["insurer"] == "AOK")
    assert D(aok["total"]) == sum((D(v) for v in aok["data"].values()), D(0)) and aok["due_date"] > aok["submit_by"]
    assert (await api.post(f"/api/payroll/statements/{aok['id']}/submitted")).status_code == 204

    # Lohnsteuerbescheinigung
    certs = (await api.post(f"/api/payroll/certificates/{TODAY.year}")).json()
    c = next(x for x in certs if x["employee_id"] == eid)
    assert D(c["3"]) == D(s["gross"]) and D(c["4"]) == D(s["lst"]) and D(c["23a"]) == D(s["rv_ee"])
    cid = (await api.get(f"/api/payroll/certificates?year={TODAY.year}")).json()[0]["id"]
    pdf = await api.get(f"/api/payroll/certificates/{cid}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


async def test_unpaid_leave_reduces_salary(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    emp = (await api.post("/api/payroll/employees", json={**CARLA, "email": "dora@firma.de", "first_name": "Dora", "car_list_price": None,
                                                          "social_security_number": None, "tax_id": None})).json()
    start = FIRST + dt.timedelta(days=2)
    assert (await api.post("/api/absences", json={"employee_id": emp["id"], "kind": "unpaid", "start_date": start.isoformat(),
                                                  "end_date": (start + dt.timedelta(days=9)).isoformat()})).status_code == 201
    run = (await api.post(f"/api/payroll/runs/{PERIOD}", json={})).json()
    s = next(p for p in run["payslips"] if p["name"].startswith("Dora"))
    # 10 Kalendertage × 4500/30 = 1.500 € Abzug
    assert D(s["gross"]) == D("3000.00")
    assert any("Unbezahlter Urlaub" in n for n in s["details"]["notes"])
