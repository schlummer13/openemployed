"""Gesellschafter/Gehälter und Kanban gegen echtes PostgreSQL."""

from __future__ import annotations

import pytest

from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
MONTH_START = today_berlin().replace(day=1)


async def test_shareholders_and_salaries(env: Env) -> None:  # noqa: F811
    api = env.api
    for name in ("Anna", "Ben", "Cem", "Dana"):
        assert (await api.post("/api/shareholders", json={"name": name, "share_percent": "25"})).status_code == 201
    r = await api.post("/api/shareholders", json={"name": "Eve", "share_percent": "1"})
    assert r.status_code == 409 and "100" in r.json()["detail"]

    overview = (await api.get("/api/team")).json()
    ids = [s["id"] for s in overview["shareholders"]]
    assert overview["share_total"] == "100.00" and len(ids) == 4

    backdated = {"shareholder_ids": ids, "valid_from": "2020-01-01", "gross_month": "3000"}
    r = await api.post("/api/compensations", json=backdated)
    assert r.status_code == 409 and "Rückwirkungsverbot" in r.json()["detail"]

    ok = {"shareholder_ids": ids, "valid_from": MONTH_START.isoformat(), "gross_month": "3000"}
    assert (await api.post("/api/compensations", json=ok)).status_code == 201
    assert (await api.post("/api/compensations", json=ok)).status_code == 409

    overview = (await api.get("/api/team")).json()
    s0 = overview["shareholders"][0]["salary"]
    assert s0["gross_month"] == "3000.00" and float(s0["net_month"]) < 3000 < float(s0["employer_cost_month"])
    assert overview["capacity"]["managing_directors"] == 4
    from decimal import Decimal

    assert Decimal(overview["capacity"]["current_employer_cost"]) == Decimal(s0["employer_cost_month"]) * 4
    assert len(overview["distribution"]["rows"]) == 4


async def test_kanban(env: Env) -> None:  # noqa: F811
    api = env.api
    pid = (await api.post("/api/projects", json={"name": "Onboarding HR 1"})).json()["id"]
    board = (await api.get(f"/api/projects/{pid}/board")).json()
    cols = {c["name"]: c["id"] for c in board["columns"]}
    assert list(cols) == ["Backlog", "Zu erledigen", "In Arbeit", "Review", "Erledigt"]

    r = await api.post(f"/api/projects/{pid}/tasks", json={"column_id": cols["Zu erledigen"], "title": "Zugänge einrichten",
                                                            "assignee": "Anna@Beispiel.de", "priority": "hoch"})
    assert r.status_code == 201, r.text
    t1 = r.json()["id"]
    t2 = (await api.post(f"/api/projects/{pid}/tasks", json={"column_id": cols["Zu erledigen"], "title": "Schulung"})).json()["id"]
    # t2 vor t1 einsortieren
    assert (await api.post(f"/api/tasks/{t2}/move", json={"column_id": cols["Zu erledigen"], "before_id": t1})).status_code == 204
    board = (await api.get(f"/api/projects/{pid}/board")).json()
    todo = next(c for c in board["columns"] if c["name"] == "Zu erledigen")
    assert [t["title"] for t in todo["tasks"]] == ["Schulung", "Zugänge einrichten"]
    # nach „Erledigt“ → erledigt-Zeitpunkt gesetzt, zurück → entfernt
    await api.post(f"/api/tasks/{t1}/move", json={"column_id": cols["Erledigt"]})
    board = (await api.get(f"/api/projects/{pid}/board")).json()
    done = next(c for c in board["columns"] if c["name"] == "Erledigt")
    assert done["tasks"][0]["completed_at"] is not None
    await api.post(f"/api/tasks/{t1}/move", json={"column_id": cols["In Arbeit"]})
    board = (await api.get(f"/api/projects/{pid}/board")).json()
    wip = next(c for c in board["columns"] if c["name"] == "In Arbeit")
    assert wip["tasks"][0]["completed_at"] is None
    assert (await api.put(f"/api/tasks/{t1}", json={"title": "Zugänge einrichten (SSO)", "priority": "dringend"})).status_code == 204
    assert (await api.delete(f"/api/tasks/{t2}")).status_code == 204
    projects = (await api.get("/api/projects")).json()
    assert projects[0]["task_count"] == 1
    # viele Umsortierungen hintereinander bleiben stabil (Neuverteilung der Positionen)
    ids = [(await api.post(f"/api/projects/{pid}/tasks", json={"column_id": cols["Backlog"], "title": f"T{i}"})).json()["id"] for i in range(3)]
    for _ in range(60):
        await api.post(f"/api/tasks/{ids[2]}/move", json={"column_id": cols["Backlog"], "before_id": ids[1]})
        await api.post(f"/api/tasks/{ids[1]}/move", json={"column_id": cols["Backlog"], "before_id": ids[2]})
    board = (await api.get(f"/api/projects/{pid}/board")).json()
    backlog = next(c for c in board["columns"] if c["name"] == "Backlog")
    assert [t["title"] for t in backlog["tasks"]] == ["T0", "T1", "T2"]


async def test_tax_profile_changes_salary(env: Env) -> None:  # noqa: F811
    api = env.api
    await api.post("/api/shareholders", json={"name": "Anna", "share_percent": "25"})
    first = today_berlin().replace(day=1).isoformat()
    team = (await api.get("/api/team")).json()
    sid = team["shareholders"][0]["id"]
    await api.post("/api/compensations", json={"shareholder_ids": [sid], "valid_from": first, "gross_month": "5000.00"})
    before = (await api.get("/api/team")).json()["shareholders"][0]
    assert before["tax_profile"] is None
    profile = {"valid_from": first, "tax_class": 3, "child_allowances": "1.0", "church_tax": True, "state": "NW", "birth_year": 1990,
               "children_under_25": 2, "health_insurance": "gkv", "health_insurer": "TK", "kv_additional_rate": "2.45"}
    assert (await api.post(f"/api/shareholders/{sid}/tax-profile", json=profile)).status_code == 201
    assert (await api.post(f"/api/shareholders/{sid}/tax-profile", json=profile)).status_code == 409, "gleiches Datum doppelt"
    assert (await api.post(f"/api/shareholders/{sid}/tax-profile", json=profile | {"child_allowances": "0.3",
                                                                                    "valid_from": "2030-01-01"})).status_code == 422
    after = (await api.get("/api/team")).json()["shareholders"][0]
    assert after["tax_profile"]["tax_class"] == 3 and after["tax_profile"]["health_insurer"] == "TK"
    assert float(after["salary"]["lst_month"]) < float(before["salary"]["lst_month"]), "Klasse III senkt die Lohnsteuer"
    assert float(after["salary"]["church_month"]) > 0


async def test_project_search_and_delete(env: Env) -> None:  # noqa: F811
    api = env.api
    a = (await api.post("/api/projects", json={"name": "Onboarding Rheinwerk", "description": "SSO und Datenübernahme"})).json()["id"]
    b = (await api.post("/api/projects", json={"name": "Vertrieb Q4"})).json()["id"]
    cols = {c["name"]: c["id"] for c in (await api.get(f"/api/projects/{b}/board")).json()["columns"]}
    await api.post(f"/api/projects/{b}/tasks", json={"column_id": cols["Zu erledigen"], "title": "Angebot Nordlicht nachfassen"})
    await api.post(f"/api/projects/{b}/tasks", json={"column_id": cols["Backlog"], "title": "100%_Rabatt prüfen"})

    r = (await api.get("/api/projects/search", params={"q": "rheinwerk"})).json()
    assert [p["name"] for p in r["projects"]] == ["Onboarding Rheinwerk"] and r["tasks"] == []
    r = (await api.get("/api/projects/search", params={"q": "nordlicht"})).json()
    assert [t["title"] for t in r["tasks"]] == ["Angebot Nordlicht nachfassen"]
    assert r["tasks"][0]["project_name"] == "Vertrieb Q4" and r["tasks"][0]["column_name"] == "Zu erledigen"
    assert [p["name"] for p in (await api.get("/api/projects/search", params={"q": "datenübernahme"})).json()["projects"]] == [
        "Onboarding Rheinwerk"], "Beschreibung wird durchsucht"
    # Platzhalter % und _ werden wörtlich gesucht
    assert [t["title"] for t in (await api.get("/api/projects/search", params={"q": "0%_"})).json()["tasks"]] == ["100%_Rabatt prüfen"]
    assert (await api.get("/api/projects/search", params={"q": "x"})).status_code == 422

    assert (await api.delete(f"/api/projects/{b}")).status_code == 204
    assert [p["id"] for p in (await api.get("/api/projects")).json()] == [a]
    assert (await api.get(f"/api/projects/{b}/board")).status_code == 404
    assert (await api.get("/api/projects/search", params={"q": "nordlicht"})).json()["tasks"] == [], "Aufgaben gelöschter Projekte verschwinden"
    assert (await api.delete(f"/api/projects/{b}")).status_code == 404
