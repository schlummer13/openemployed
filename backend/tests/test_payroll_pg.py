"""Lohnbuchhaltung: Mitarbeiter, Lohnlauf, Lohnbuchung, Zahlungen, PDF und Mitarbeiter-Portal."""

from __future__ import annotations

import calendar
from decimal import Decimal

import pytest

from app.main import app
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401
from tests.test_portal_pg import last_code

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal
PERIOD = f"{TODAY:%Y-%m}"
FIRST = TODAY.replace(day=1).isoformat()

ANNA = {"first_name": "Anna", "last_name": "Schmidt", "email": "Anna@Firma.de", "entry_date": "2025-01-01", "birth_date": "1990-05-17",
        "social_security_number": "12170590S123", "tax_id": "12345678901", "child_allowances": "0.5", "church_tax": True,
        "church_denomination": "rk",
        "salary_type": "monthly", "gross_month": "4000.00", "tax_class": 1, "iban": "DE89 3704 0044 0532 0130 00",
        "health_insurer": "TK", "kv_additional_rate": "2.9", "portal_enabled": True}
MAX = {"first_name": "Max", "last_name": "Minijob", "entry_date": "2025-06-01", "employment_type": "minijob",
       "salary_type": "hourly", "hourly_rate": "14.00", "weekly_hours": "8", "iban": "DE02120300000000202051"}


async def test_payroll_run_booking_and_portal(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    # Plausibilitätsprüfungen
    assert (await api.post("/api/payroll/employees", json={**MAX, "hourly_rate": "12.00"})).status_code == 409, "Mindestlohn"
    assert (await api.post("/api/payroll/employees", json={**MAX, "weekly_hours": "20"})).status_code == 409, "Minijob-Grenze"
    assert (await api.post("/api/payroll/employees", json={**ANNA, "iban": "DE00123"})).status_code == 422
    anna = (await api.post("/api/payroll/employees", json=ANNA)).json()
    maxi = (await api.post("/api/payroll/employees", json=MAX)).json()
    assert (await api.post("/api/payroll/employees", json={**ANNA, "first_name": "Doppelt"})).status_code == 409, "E-Mail doppelt"
    emps = (await api.get("/api/payroll/employees")).json()["employees"]
    assert [e["number"] for e in emps] == [2, 1] and emps[1]["email"] == "anna@firma.de"
    # Gesellschafter-GF mit SV-freiem Gehalt kommt automatisch in den Lohnlauf
    gf = (await api.post("/api/shareholders", json={"name": "Gina GF", "share_percent": "100"})).json()["id"]
    assert (await api.post("/api/compensations", json={"shareholder_ids": [gf], "valid_from": FIRST, "gross_month": "5000.00",
                                                        "sv_status": "frei"})).status_code == 201

    preview = (await api.get(f"/api/payroll/runs/{PERIOD}")).json()
    assert preview["status"] == "none" and len(preview["payslips"]) == 3
    run = (await api.post(f"/api/payroll/runs/{PERIOD}", json={"hours": {maxi["id"]: "35"}})).json()
    slips = {p["name"]: p for p in run["payslips"]}
    a = slips["Anna Schmidt"]
    assert D(a["gross"]) == D("4000.00") and D(a["rv_ee"]) == D("372.00") and D(a["kv_ee"]) == D("350.00")
    assert D(a["net"]) == D(a["gross"]) - D(a["lst"]) - D(a["solz"]) - D(a["church"]) - D(a["rv_ee"]) - D(a["av_ee"]) \
        - D(a["kv_ee"]) - D(a["pv_ee"])
    m = slips["Max Minijob"]
    assert D(m["gross"]) == D("490.00") and D(m["hours"]) == D("35.00") and D(m["rv_er"]) == D("73.50") and D(m["flat_tax"]) == D("9.80")
    g = slips["Gina GF"]
    assert g["employment_type"] == "shareholder" and D(g["rv_ee"]) == 0 and D(g["lst"]) > 0
    # Neuberechnung übernimmt die Stunden
    again = (await api.post(f"/api/payroll/runs/{PERIOD}", json={})).json()
    slips = {p["name"]: p for p in again["payslips"]}
    a, g = slips["Anna Schmidt"], slips["Gina GF"]
    assert D(slips["Max Minijob"]["hours"]) == D("35.00")

    pdf = await api.get(f"/api/payroll/payslips/{a['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    approved = (await api.post(f"/api/payroll/runs/{PERIOD}/approve")).json()
    assert approved["status"] == "approved"
    t = approved["totals"]
    assert (await api.post(f"/api/payroll/runs/{PERIOD}", json={})).status_code == 409, "freigegeben = fest"
    assert (await api.delete(f"/api/payroll/runs/{PERIOD}")).status_code == 409
    last = TODAY.replace(day=calendar.monthrange(TODAY.year, TODAY.month)[1]).isoformat()
    entries = (await api.get("/api/accounting/journal", params={"start": FIRST, "end": last})).json()["entries"]
    booking = next(e for e in entries if e["source_type"] == "payroll")
    lines = {ln["account"]: (D(ln["debit"]), D(ln["credit"])) for ln in booking["lines"]}
    assert lines["6020"][0] == D(a["gross"]) + D(g["gross"]) and lines["6035"][0] == D("490.00")
    assert lines["3720"][1] == D(t["payout"]) and lines["3730"][1] == D(t["taxes"]) and lines["3740"][1] == D(t["sv_total"])
    assert sum(v[0] for v in lines.values()) == sum(v[1] for v in lines.values())

    from app.domain.sepa import next_business_day

    exe = next_business_day(TODAY, 1).isoformat()
    no_iban = await api.post("/api/sepa/salaries", json={"period": PERIOD, "execution_date": exe})
    assert no_iban.status_code == 409 and "Gina GF" in no_iban.text
    assert (await api.put(f"/api/shareholders/{gf}", json={"name": "Gina GF", "share_percent": "100",
                                                         "iban": "DE02500105170137075030"})).status_code == 204
    fid = (await api.post("/api/sepa/salaries", json={"period": PERIOD, "execution_date": exe})).json()["id"]
    sepa_xml = (await api.get(f"/api/sepa/files/{fid}.xml")).text
    assert sepa_xml.count("<CdtTrfTxInf>") == 3 and f"<CtrlSum>{t['payout']}</CtrlSum>" in sepa_xml
    assert (await api.post(f"/api/payroll/runs/{PERIOD}/paid", json={"what": "salaries", "paid_on": TODAY.isoformat()})).status_code == 200
    assert (await api.post(f"/api/payroll/runs/{PERIOD}/paid", json={"what": "salaries", "paid_on": TODAY.isoformat()})).status_code == 409
    lsta = (await api.get(f"/api/payroll/runs/{PERIOD}/lsta")).json()["values"]
    assert lsta["employees"] == 2 and D(lsta["lst"]) == D(a["lst"]) + D(g["lst"])

    # Self-Service: Anmeldung per Code, nur eigene, freigegebene Abrechnungen
    await api.post("/api/portal/login/request", json={"email": "anna@firma.de"})
    r = await api.post("/api/portal/login/verify", json={"email": "anna@firma.de", "code": last_code()})
    assert r.status_code == 200 and r.json()["employee"] is True
    me = (await api.get("/api/portal/me")).json()
    assert me["employee"]["name"] == "Anna Schmidt"
    ov = (await api.get("/api/portal/employee")).json()
    assert len(ov["payslips"]) == 1 and ov["profile"]["iban"].endswith("3000") and "•" in ov["profile"]["iban"]
    own = await api.get(f"/api/portal/employee/payslips/{ov['payslips'][0]['id']}/pdf")
    assert own.status_code == 200 and own.content.startswith(b"%PDF")
    # Pflichtangaben der Entgeltbescheinigung (§ 108 GewO, EBV)
    from uuid import UUID

    from app.core.config import get_settings
    from app.services import payroll as ps

    async with env.engine.connect() as conn:
        slip = await ps.get_payslip(conn, get_settings().tenant_id, UUID(ov["payslips"][0]["id"]))
        employee = await ps.get_employee(conn, get_settings().tenant_id, slip["employee_id"])
        ytd = await ps.year_to_date(conn, get_settings().tenant_id, slip)
    html = ps.payslip_html(slip, None, employee, ytd, None)
    for needle in ("Entgeltbescheinigung", "§ 108 Abs. 3 Satz 1 GewO", "17.05.1990", "12170590S123", "12345678901", "01.01.2025",
                   "rk (römisch-katholisch)", "0,5", "1 1 1 1", "101 (sozialversicherungspflichtig", "SV-Tage", "SV-Brutto RV/AV",
                   "Steuerrechtliches Brutto", "Jahreswerte", "Gehalt", "Auszahlungsbetrag", "Personalnummer"):
        assert needle in html, needle
    assert slip["details"]["sv_days"] == 30 and slip["details"]["contribution_group"] == "1111" and D(ytd["gross"]) >= D("4000")
    assert (await api.get(f"/api/portal/employee/payslips/{g['id']}/pdf")).status_code == 404, "fremde Abrechnung"
    assert (await api.put("/api/portal/employee", json={"city": "Köln", "iban": "DE02120300000000202051"})).status_code == 204
    assert (await api.put("/api/portal/employee", json={"tax_class": 3})).status_code == 422, "Steuerdaten nicht selbst änderbar"
    assert (await api.get("/api/portal/overview")).status_code == 409, "kein Kundenzugang"
    emp = next(e for e in (await api.get("/api/payroll/employees")).json()["employees"] if e["first_name"] == "Anna")
    assert emp["city"] == "Köln" and emp["iban"] == "DE02120300000000202051"

    # Archiviert → kein Portal mehr
    assert (await api.delete(f"/api/payroll/employees/{anna['id']}")).status_code == 204
    assert (await api.get("/api/portal/employee")).status_code == 401
    assert len(app.state.mailer.sent) >= 1
