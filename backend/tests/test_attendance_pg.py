"""Stempeluhr, Urlaubskonto, Antrag/Genehmigung, Team-Kalender und Lohn aus Stempelzeiten."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401
from tests.test_portal_pg import last_code

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal
YEAR = dt.datetime.now(dt.UTC).year + 1  # Urlaub im Folgejahr: unabhängig vom heutigen Datum


async def test_clock_vacation_calendar(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    emp = (await api.post("/api/payroll/employees", json={
        "first_name": "Lea", "last_name": "Teil", "email": "lea@firma.de", "entry_date": f"{YEAR}-08-15", "salary_type": "hourly",
        "hourly_rate": "15.00", "weekly_hours": "24", "vacation_days": 18, "work_days": [0, 1, 2], "portal_enabled": True,
        "iban": "DE02120300000000202051"})).json()
    # Urlaubsrechner: Eintritt 15.8. → 4 volle Monate (15.8.–14.12.) → 18 × 4/12 = 6
    pv = (await api.post("/api/absences/preview", json={"entry_date": f"{YEAR}-08-15", "vacation_days": 18, "work_days": [0, 1, 2]})).json()
    first = next(y for y in pv["years"] if y["year"] == YEAR)
    assert D(first["days"]) == 6 and "30.6." in first["rule"] and D(first["statutory_minimum"]) == 12

    # Mitarbeiter-Portal: Stempeluhr
    await api.post("/api/portal/login/request", json={"email": "lea@firma.de"})
    assert (await api.post("/api/portal/login/verify", json={"email": "lea@firma.de", "code": last_code()})).status_code == 200
    st = (await api.get("/api/portal/employee/clock")).json()
    assert st["open"] is None
    assert (await api.post("/api/portal/employee/clock/in")).status_code == 204
    assert (await api.post("/api/portal/employee/clock/in")).status_code == 409, "doppelt eingestempelt"
    assert (await api.get("/api/portal/employee/clock")).json()["open"] is not None
    # 7 Std. Anwesenheit simulieren → 30 Min. Pause
    async with env.engine.begin() as conn:
        await conn.execute(text("UPDATE time_clock SET started_at = now() - interval '7 hours' WHERE ended_at IS NULL"))
    assert (await api.post("/api/portal/employee/clock/out", json={"note": "Projekt X"})).status_code == 204
    st = (await api.get("/api/portal/employee/clock")).json()
    e = st["days"][0]["entries"][0]
    assert st["open"] is None and e["break_minutes"] == 30 and e["worked_minutes"] == 390
    # zweiter Block am selben Tag: 3 Std. → Tagessumme 10 Std. → weitere 15 Min. Pause (45 gesamt)
    assert (await api.post("/api/portal/employee/clock/in")).status_code == 204
    async with env.engine.begin() as conn:
        await conn.execute(text("UPDATE time_clock SET started_at = now() - interval '3 hours' WHERE ended_at IS NULL"))
    await api.post("/api/portal/employee/clock/out", json={})
    st = (await api.get("/api/portal/employee/clock")).json()
    if len({d["date"] for d in st["days"]}) == 1:  # nicht über Mitternacht
        day = st["days"][0]
        assert sum(x["break_minutes"] for x in day["entries"]) == 45 and day["net"] == 555

    # Urlaub beantragen (nur Mo–Mi sind Arbeitstage)
    ab = (await api.get("/api/portal/employee/absences")).json()
    assert ab["employee"]["work_days"] == [0, 1, 2]
    nb = ab["next_balance"] if ab["next_balance"]["year"] == YEAR else ab["balance"]
    assert D(nb["entitlement"]) == 6
    mon = dt.date(YEAR, 9, 1) + dt.timedelta(days=(7 - dt.date(YEAR, 9, 1).weekday()) % 7)
    req = {"kind": "vacation", "start_date": mon.isoformat(), "end_date": (mon + dt.timedelta(days=6)).isoformat()}
    r = await api.post("/api/portal/employee/absences", json=req)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert (await api.post("/api/portal/employee/absences", json=req)).status_code == 409, "Überschneidung"
    too_much = {"kind": "vacation", "start_date": f"{YEAR}-10-01", "end_date": f"{YEAR}-11-30"}
    assert (await api.post("/api/portal/employee/absences", json=too_much)).status_code == 409, "nicht genug Urlaub"

    # Geschäftsführung: Team-Kalender und Genehmigung
    cal = (await api.get(f"/api/absences/calendar?month={mon:%Y-%m}")).json()
    row = next(x for x in cal["employees"] if x["id"] == emp["id"])
    a = row["absences"][0]
    assert D(a["days"]) == 3 and a["status"] == "requested" and len(cal["pending"]) == 1
    assert (await api.post(f"/api/absences/{aid}/decide", json={"approve": True, "note": "Viel Erholung"})).json()["status"] == "approved"
    assert (await api.post(f"/api/absences/{aid}/decide", json={"approve": False})).status_code == 409
    # Krankmeldung durch Personal: sofort genehmigt
    sick = {"employee_id": emp["id"], "kind": "sick", "start_date": f"{YEAR}-09-30", "end_date": f"{YEAR}-09-30"}
    assert (await api.post("/api/absences", json=sick)).status_code == 201
    cal = (await api.get(f"/api/absences/calendar?month={YEAR}-09")).json()
    row = next(x for x in cal["employees"] if x["id"] == emp["id"])
    assert {x["status"] for x in row["absences"]} == {"approved"} and D(row["remaining"]) == 3
    # Zeitkonto für Personal
    month = st["days"][0]["date"][:7]
    tc = (await api.get(f"/api/timeclock?month={month}&employee_id={emp['id']}")).json()
    assert len(tc) == 2 and {t["source"] for t in tc} == {"portal"}
    # Korrektur mit Begründung
    start = tc[0]["started_at"]
    end = (dt.datetime.fromisoformat(start) + dt.timedelta(hours=8)).isoformat()
    assert (await api.put(f"/api/timeclock/{tc[0]['id']}", json={"started_at": start, "ended_at": end, "reason": "x"})).status_code == 422
    assert (await api.put(f"/api/timeclock/{tc[0]['id']}", json={"started_at": start, "ended_at": end,
                                                                 "reason": "Ausstempeln vergessen"})).status_code == 204


async def test_holiday_calendar_company_days_ical(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    cal = (await api.get("/api/holidays?year=2031&state=NW")).json()
    assert cal["statutory_count"] == 11 and cal["easter"] == "2031-04-13" and any(s["name"] == "Rosenmontag" for s in cal["suggestions"])
    assert (await api.get("/api/holidays?year=2031&state=XX")).status_code == 409
    # Heiligabend halb, Rosenmontag ganz frei (wandert mit Ostern)
    assert (await api.post("/api/holidays/company", json={"name": "Heiligabend", "month": 12, "day": 24, "half_day": True})).status_code == 201
    assert (await api.post("/api/holidays/company", json={"name": "Rosenmontag", "rule": "easter", "easter_offset": -48})).status_code == 201
    assert (await api.post("/api/holidays/company", json={"name": "x", "rule": "easter"})).status_code == 422
    cal = (await api.get(f"/api/holidays?year={YEAR}&state=NW")).json()
    company = {i["name"]: i for i in cal["items"] if i["kind"] == "company"}
    assert company["Heiligabend"]["half_day"] and "Rosenmontag" in company and not any(s["name"] == "Rosenmontag" for s in cal["suggestions"])
    # Urlaub über Weihnachten zählt Heiligabend nur halb
    emp = (await api.post("/api/payroll/employees", json={
        "first_name": "Tom", "last_name": "Voll", "email": "tom@firma.de", "entry_date": f"{YEAR - 2}-01-01", "salary_type": "monthly",
        "gross_month": "3500.00", "weekly_hours": "40", "vacation_days": 30, "iban": "DE02120300000000202051"})).json()
    xmas = dt.date(YEAR, 12, 24)
    if xmas.weekday() < 5:
        r = await api.post("/api/absences", json={"employee_id": emp["id"], "kind": "vacation", "start_date": xmas.isoformat(),
                                                  "end_date": xmas.isoformat()})
        assert r.status_code == 201
        month = (await api.get(f"/api/absences/calendar?month={YEAR}-12")).json()
        a = next(x for x in month["employees"] if x["id"] == emp["id"])["absences"][0]
        assert D(a["days"]) == D("0.5") and month["half_days"]["NW"][xmas.isoformat()] == "Heiligabend"
    # iCal für mehrere Jahre im Voraus
    ics = await api.get("/api/holidays/ical?state=NW&start=2030&end=2034")
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    body = ics.text
    assert body.count("BEGIN:VEVENT") == 5 * 11 + 5 * 2 and "SUMMARY:Fronleichnam" in body and "DTSTART;VALUE=DATE:20340528" not in body
    rid = (await api.get("/api/holidays?year=2030&state=NW")).json()["rules"][0]["id"]
    assert (await api.delete(f"/api/holidays/company/{rid}")).status_code == 204
