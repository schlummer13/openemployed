"""Personal: Zeitkonto mit Überstunden-Auszahlung, On-/Offboarding-Checklisten, Vertragsvorlage → PDF, Personalakte, Löschung."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal


async def test_time_account_checklists_personnel_file(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    today = dt.date.today()
    start = today - dt.timedelta(days=today.weekday() + 7)  # Montag der Vorwoche
    emp = (await api.post("/api/payroll/employees", json={
        "first_name": "Jonas", "last_name": "Konto", "email": "jonas@firma.de", "entry_date": (start - dt.timedelta(days=60)).isoformat(),
        "salary_type": "monthly", "gross_month": "3466.67", "weekly_hours": "40", "vacation_days": 28, "time_account_start": start.isoformat(),
        "overtime_cap_hours": "1", "iban": "DE02120300000000202051", "state": "NW"})).json()
    eid = emp["id"]

    # Onboarding-Checkliste wurde automatisch angelegt
    cl = (await api.get(f"/api/personnel/{eid}/checklists")).json()
    assert len(cl) == 1 and cl[0]["kind"] == "onboarding" and cl[0]["total"] >= 10
    assert any("DEÜV" in (i["legal"] or "") for i in cl[0]["items"])
    first = cl[0]["items"][0]["id"]
    assert (await api.post(f"/api/personnel/checklist-items/{first}", json={"done": True})).status_code == 204
    assert (await api.get(f"/api/personnel/{eid}/checklists")).json()[0]["done"] == 1
    assert (await api.post(f"/api/personnel/checklists/{cl[0]['id']}/items", json={"title": "Schlüssel ausgeben"})).status_code == 204

    # Zeitkonto: Montag der Vorwoche 10 Std. Anwesenheit (= 9:15 netto) → +75 Min., Dienstag Überstundenabbau ganz
    async with env.engine.begin() as conn:
        mon = dt.datetime.combine(start, dt.time(8, 0), tzinfo=dt.UTC)
        await conn.execute(text("INSERT INTO time_clock (tenant_id, employee_id, started_at, ended_at, break_minutes, worked_minutes) "
                                "SELECT tenant_id, id, :s, :e, 45, 555 FROM employees WHERE id = :id"),
                           {"s": mon, "e": mon + dt.timedelta(hours=10), "id": eid})
    r = await api.post("/api/absences", json={"employee_id": eid, "kind": "overtime", "start_date": (start + dt.timedelta(days=1)).isoformat(),
                                              "end_date": (start + dt.timedelta(days=1)).isoformat()})
    assert r.status_code == 201
    acc = (await api.get(f"/api/personnel/{eid}/time-account?")).json()
    assert acc["employee"]["daily_target"] == 480
    acc_days = {m["month"]: m for m in acc["months"]}
    assert acc_days, acc
    # Saldo: Mo +75; Di −480 (Abbau); Mi–Fr ohne Stempeln je −480 (Feiertage ausgenommen) → deutlich negativ
    assert acc["balance"] < 0 and acc["start"] == start.isoformat()

    # Übertrag aus dem Altsystem und Auszahlung → Lohnposition
    assert (await api.post(f"/api/personnel/{eid}/time-account/bookings", json={
        "kind": "opening", "minutes": 3000, "booked_on": start.isoformat(), "reason": "Übertrag Altsystem"})).status_code == 201
    period = f"{today:%Y-%m}"
    r = await api.post(f"/api/personnel/{eid}/time-account/bookings", json={
        "kind": "payout", "minutes": 120, "booked_on": today.isoformat(), "reason": "Auszahlung 2 Std.", "payroll_period": period,
        "surcharge_percent": 25})
    assert r.status_code == 201
    async with env.engine.connect() as conn:
        item = (await conn.execute(text("SELECT quantity, rate, amount FROM payroll_items WHERE employee_id = :e"), {"e": eid})).one()
    # Stundenwert 3466,67 / (40 × 13/3) = 20,00 → +25 % = 25,00 → 2 Std. = 50,00
    assert (item.quantity, item.rate, item.amount) == (D("2.00"), D("25.00"), D("50.00"))
    acc2 = (await api.get(f"/api/personnel/{eid}/time-account")).json()
    assert acc2["balance"] == acc["balance"] + 3000 - 120
    overview = (await api.get("/api/personnel/time-accounts")).json()
    assert any(o["employee_id"] == eid for o in overview)

    # Vertragsvorlage → PDF in der Personalakte
    tpls = (await api.get("/api/personnel/contract-templates")).json()
    assert tpls["templates"] and "vertrag.verguetung" in tpls["placeholders"]
    r = await api.post(f"/api/personnel/{eid}/contracts", json={"template_id": tpls["templates"][0]["id"]})
    assert r.status_code == 201 and r.json()["missing"] == []
    docs = (await api.get(f"/api/personnel/{eid}/documents")).json()["documents"]
    contract = docs[0]
    assert contract["category"] == "contract" and contract["mime_type"] == "application/pdf" and contract["visible_to_employee"]
    pdf = await api.get(f"/api/personnel/documents/{contract['id']}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # Upload eines vertraulichen Dokuments (nicht für den Mitarbeiter sichtbar)
    up = await api.post(f"/api/personnel/{eid}/documents", data={"category": "warning", "title": "Abmahnung"},
                        files={"file": ("abmahnung.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert up.status_code == 201
    assert (await api.post(f"/api/personnel/{eid}/documents", data={"category": "other", "title": "x"},
                           files={"file": ("x.exe", b"MZ", "application/x-msdownload")})).status_code == 409

    # Austritt → Offboarding-Checkliste und Löschfrist
    emp_full = next(e for e in (await api.get("/api/payroll/employees")).json()["employees"] if e["id"] == eid)
    body = {k: emp_full[k] for k in ("first_name", "last_name", "email", "entry_date", "salary_type", "gross_month", "weekly_hours",
                                     "vacation_days", "iban", "state", "time_account_start", "overtime_cap_hours")}
    exit_ = today + dt.timedelta(days=90)
    assert (await api.put(f"/api/payroll/employees/{eid}", json=body | {"exit_date": exit_.isoformat()})).status_code == 200
    kinds = {c["kind"] for c in (await api.get(f"/api/personnel/{eid}/checklists")).json()}
    assert kinds == {"onboarding", "offboarding"}
    docs = (await api.get(f"/api/personnel/{eid}/documents")).json()["documents"]
    assert all(d["delete_after"] == f"{exit_.year + 3}-12-31" for d in docs)
    # DSGVO-Löschung: Inhalt weg, Nachweis bleibt
    warn = next(d for d in docs if d["category"] == "warning")
    assert (await api.post(f"/api/personnel/documents/{warn['id']}/delete", json={"reason": "Abmahnung nach 2 Jahren entfernt"})).status_code == 204
    assert (await api.get(f"/api/personnel/documents/{warn['id']}/file")).status_code == 404
    tasks = (await api.get("/api/inbox")).json()["tasks"]
    assert any("Personal-Aufgabe" in t["title"] for t in tasks)
