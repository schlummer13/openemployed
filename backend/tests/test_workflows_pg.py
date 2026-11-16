"""Eigene Workflows: Ereignis-, Cron- und manueller Auslöser, Bedingung, E-Mail, Aufgabe, Python (Sandbox-Attrappe)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import get_settings
from app.main import app
from app.services import workflows
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


async def test_event_workflow_with_condition_mail_task(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    pid = (await api.post("/api/projects", json={"name": "Vertrieb"})).json()["id"]
    wf = {"name": "Neukunde begrüßen", "enabled": True, "trigger_type": "event", "trigger_value": "customer.created", "steps": [
        {"id": "nur_firmen", "type": "condition", "config": {"left": "{{data.customer_type}}", "op": "==", "right": "b2b"}},
        {"id": "mail", "type": "email", "config": {"to": "vertrieb@beispiel.de", "subject": "Neukunde {{data.company_name}}",
                                                   "body": "Bitte anrufen: {{data.company_name}}"}},
        {"id": "aufgabe", "type": "task", "config": {"project_id": pid, "title": "Onboarding {{data.company_name}}", "due_in_days": "3"}},
    ]}
    bad = await api.post("/api/workflows", json={**wf, "trigger_value": "gibtsnicht"})
    assert bad.status_code == 409
    bad2 = await api.post("/api/workflows", json={**wf, "steps": [{"id": "x", "type": "email", "config": {"to": "a@b.de"}}]})
    assert bad2.status_code == 409 and "subject" in bad2.text
    assert (await api.post("/api/workflows", json=wf)).status_code == 201

    await api.post("/api/customers", json={**B2B, "company_name": "Neu GmbH", "email": "neu@gmbh.de"})
    await api.post("/api/customers", json={"customer_type": "b2c", "first_name": "Max", "last_name": "P", "email": "m@p.de",
                                           "street": "Weg 1", "postal_code": "40210", "city": "Düsseldorf"})
    ctx = SimpleNamespace(engine=env.engine, mailer=app.state.mailer, tenant_id=get_settings().tenant_id)
    stats = await workflows.process_queue(ctx, dt.datetime.now(ZoneInfo("Europe/Berlin")))
    assert stats["succeeded"] == 1 and stats["stopped"] >= 1, stats
    assert any(m.subject == "Neukunde Neu GmbH" for m in app.state.mailer.sent)
    board = (await api.get(f"/api/projects/{pid}/board")).json()
    titles = [t["title"] for t in board["tasks"]] if "tasks" in board else [t["title"] for c in board["columns"] for t in c.get("tasks", [])]
    assert "Onboarding Neu GmbH" in titles
    runs = (await api.get("/api/workflows")).json()["runs"]
    assert {r["status"] for r in runs} >= {"succeeded", "stopped"} and runs[0]["log"]


async def test_cron_and_python_step(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, *a: object, **k: object) -> None: ...
        async def __aenter__(self) -> FakeClient:
            return self
        async def __aexit__(self, *a: object) -> None: ...
        async def post(self, url: str, content: str, headers: dict) -> httpx.Response:
            import json
            req = json.loads(content)
            calls.append(req)
            return httpx.Response(200, json={"ok": True, "result": {"doppelt": req["input"]["data"]["n"] * 2}, "stdout": ""})

    monkeypatch.setattr(workflows.httpx, "AsyncClient", FakeClient)
    s = get_settings()
    old = s.sandbox_url
    object.__setattr__(s, "sandbox_url", "http://sandbox:8000")  # Settings sind eingefroren
    try:
        await _python_and_cron(api, env)
    finally:
        object.__setattr__(s, "sandbox_url", old)
    assert calls and "code" in calls[0]


async def _python_and_cron(api: httpx.AsyncClient, env: Env) -> None:  # noqa: F811
    wf = {"name": "Rechnen", "enabled": True, "trigger_type": "manual", "steps": [
        {"id": "py", "type": "python", "config": {"code": "result = {'doppelt': data['data']['n'] * 2}"}},
        {"id": "check", "type": "condition", "config": {"left": "{{steps.py.result.doppelt}}", "op": ">=", "right": "10"}}]}
    wid = (await api.post("/api/workflows", json=wf)).json()["id"]
    r = (await api.post(f"/api/workflows/{wid}/run", json={"data": {"n": 7}})).json()
    assert r["status"] == "succeeded" and r["run"]["log"][0]["result"]["result"] == {"doppelt": 14}
    assert (await api.post(f"/api/workflows/{wid}/run", json={"data": {"n": 2}})).json()["status"] == "stopped"

    cron = {"name": "Montags", "enabled": True, "trigger_type": "cron", "trigger_value": "0 8 * * 1", "steps": [
        {"id": "m", "type": "email", "config": {"to": "gf@beispiel.de", "subject": "Wochenstart {{data.time}}", "body": "Los geht's"}}]}
    assert (await api.post("/api/workflows", json={**cron, "trigger_value": "jeden Montag"})).status_code == 409
    assert (await api.post("/api/workflows", json=cron)).status_code == 201
    ctx = SimpleNamespace(engine=env.engine, mailer=app.state.mailer, tenant_id=get_settings().tenant_id)
    monday = dt.datetime(2026, 11, 16, 8, 0, 20, tzinfo=ZoneInfo("Europe/Berlin"))
    assert (await workflows.process_queue(ctx, monday))["cron"] == 1
    assert (await workflows.process_queue(ctx, monday))["cron"] == 0, "nur einmal je Minute"
    assert (await workflows.process_queue(ctx, monday + dt.timedelta(minutes=1)))["cron"] == 0
    assert any(m.subject.startswith("Wochenstart 2026-11-16T08:00") for m in app.state.mailer.sent)


async def test_if_else_branches(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    wf = {"name": "Verzweigt", "enabled": True, "trigger_type": "manual", "steps": [
        {"id": "gross", "type": "if", "config": {"left": "{{data.betrag}}", "op": ">=", "right": "1000",
         "then": [{"id": "mail_gf", "type": "email", "config": {"to": "gf@beispiel.de", "subject": "Groß: {{data.betrag}}", "body": "x"}}],
         "else": [{"id": "klein", "type": "if", "config": {"left": "{{data.betrag}}", "op": "<", "right": "10",
                   "then": [{"id": "stopp", "type": "condition", "config": {"left": "1", "op": "==", "right": "2"}}],
                   "else": [{"id": "mail_bh", "type": "email", "config": {"to": "bh@beispiel.de", "subject": "Normal", "body": "y"}}]}}]}},
        {"id": "danach", "type": "email", "config": {"to": "log@beispiel.de", "subject": "Ende {{steps.gross.branch}}", "body": "z"}}]}
    dup = {**wf, "steps": [*wf["steps"], {"id": "mail_gf", "type": "email", "config": {"to": "a@b.de", "subject": "s", "body": "b"}}]}
    assert (await api.post("/api/workflows", json=dup)).status_code == 409, "Kennungen über alle Zweige eindeutig"
    wid = (await api.post("/api/workflows", json=wf)).json()["id"]
    run = lambda n: api.post(f"/api/workflows/{wid}/run", json={"data": {"betrag": n}})  # noqa: E731
    big = (await run(5000)).json()
    assert big["status"] == "succeeded" and [e["id"] for e in big["run"]["log"]] == ["gross", "mail_gf", "danach"]
    mid = (await run(500)).json()
    assert [e["id"] for e in mid["run"]["log"]] == ["gross", "klein", "mail_bh", "danach"]
    small = (await run(5)).json()
    assert small["status"] == "stopped" and [e["id"] for e in small["run"]["log"]] == ["gross", "klein", "stopp"]
    assert any(m.subject == "Ende Nein" for m in app.state.mailer.sent) and any(m.subject == "Groß: 5000" for m in app.state.mailer.sent)
