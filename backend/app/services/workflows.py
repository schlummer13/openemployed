"""Eigene Workflows (Automatisierungen im Baukasten): Auslöser → Kette von Blöcken.

Auslöser: Cron (Europe/Berlin), fachliches Ereignis (siehe events.PUBLIC) oder manuell. Blöcke:
- ``if``         Abzweigung: je nach Bedingung läuft die Kette „Ja“ oder „Nein“ (verschachtelbar, bis 3 Ebenen)
- ``condition``  Bedingung – bei „falsch“ endet der Lauf (Status „gestoppt“)
- ``email``      E-Mail senden (über das eingerichtete Postfach)
- ``task``       Aufgabe in einem Projekt anlegen
- ``http``       HTTP-Anfrage an externe Dienste (gleicher SSRF-Schutz wie Webhooks)
- ``python``     eigener Python-Code im isolierten Sandbox-Container (``data`` rein, ``result`` raus)
Platzhalter ``{{data.…}}``, ``{{steps.<id>.…}}``, ``{{now}}`` in allen Textfeldern. Jeder Lauf wird protokolliert.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.domain.workflows import OPS, WorkflowError, condition, cron_matches, parse_cron, render
from app.services import audit, events
from app.services.masterdata import ConflictError, NotFoundError

log = logging.getLogger("selbstlite.workflows")
STEP_TYPES = {"if": "Wenn / Sonst", "condition": "Bedingung", "email": "E-Mail senden", "task": "Aufgabe anlegen", "http": "HTTP-Anfrage",
              "python": "Python-Code"}
MAX_STEPS = 40
COLUMNS = ("id, name, description, enabled, trigger_type, trigger_value, steps, last_cron_at, created_by, updated_by, created_at, "
           "updated_at")


def validate(trigger_type: str, trigger_value: str | None, steps: list[dict[str, Any]]) -> None:
    if trigger_type == "cron":
        try:
            parse_cron(trigger_value or "")
        except WorkflowError as exc:
            raise ConflictError(str(exc)) from exc
    elif trigger_type == "event" and trigger_value not in events.PUBLIC:
        raise ConflictError("Unbekanntes Ereignis")
    ids: set[str] = set()
    count = _validate_steps(steps, ids, 1, "")
    if count > MAX_STEPS:
        raise ConflictError(f"Höchstens {MAX_STEPS} Blöcke insgesamt")


def _validate_steps(steps: list[dict[str, Any]], ids: set[str], depth: int, prefix: str) -> int:
    if depth > 3:
        raise ConflictError("Abzweigungen höchstens drei Ebenen tief")
    count = 0
    for i, st in enumerate(steps, start=1):
        label = f"{prefix}{i}"
        kind, cfg = st.get("type"), st.get("config") or {}
        sid = str(st.get("id") or "")
        if kind not in STEP_TYPES:
            raise ConflictError(f"Block {label}: unbekannter Typ")
        if not sid.replace("_", "").isalnum() or sid in ids:
            raise ConflictError(f"Block {label}: Kennung fehlt oder doppelt")
        ids.add(sid)
        count += 1
        required = {"if": ("left", "op"), "condition": ("left", "op"), "email": ("to", "subject", "body"), "task": ("project_id", "title"),
                    "http": ("method", "url"), "python": ("code",)}[kind]
        missing = [k for k in required if not str(cfg.get(k) or "").strip()]
        if missing:
            raise ConflictError(f"Block {label} ({STEP_TYPES[kind]}): {', '.join(missing)} fehlt")
        if kind in ("if", "condition") and cfg["op"] not in OPS:
            raise ConflictError(f"Block {label}: Vergleich ungültig")
        if kind == "http" and cfg["method"] not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ConflictError(f"Block {label}: HTTP-Methode ungültig")
        if kind == "python" and len(cfg["code"]) > 20000:
            raise ConflictError(f"Block {label}: Code zu lang")
        if kind == "if":
            for branch, name in (("then", "Ja"), ("else", "Nein")):
                sub = cfg.get(branch) or []
                if not isinstance(sub, list):
                    raise ConflictError(f"Block {label}: Zweig {name} ungültig")
                count += _validate_steps(sub, ids, depth + 1, f"{label}.{name}.")
    return count


async def list_workflows(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        f"SELECT {COLUMNS}, (SELECT row_to_json(r) FROM (SELECT status, queued_at FROM workflow_runs WHERE workflow_id = w.id "
        "ORDER BY queued_at DESC LIMIT 1) r) AS last_run FROM workflows w WHERE tenant_id = :t ORDER BY name"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def get(conn: AsyncConnection, tenant_id: UUID, wid: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {COLUMNS} FROM workflows WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": wid})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Workflow nicht gefunden")
    return dict(row)


async def save(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str, wid: UUID | None = None) -> UUID:
    validate(data["trigger_type"], data.get("trigger_value"), data["steps"])
    params = {"t": tenant_id, "id": wid, "a": actor, "name": data["name"], "description": data.get("description"),
              "enabled": data["enabled"], "tt": data["trigger_type"], "tv": data.get("trigger_value"), "steps": json.dumps(data["steps"])}
    if wid is None:
        wid = await conn.scalar(text(
            "INSERT INTO workflows (tenant_id, name, description, enabled, trigger_type, trigger_value, steps, created_by, updated_by) "
            "VALUES (:t, :name, :description, :enabled, :tt, :tv, CAST(:steps AS jsonb), :a, :a) RETURNING id"), params)
        action = "workflow.created"
    else:
        await get(conn, tenant_id, wid)
        await conn.execute(text(
            "UPDATE workflows SET name = :name, description = :description, enabled = :enabled, trigger_type = :tt, trigger_value = :tv, "
            "steps = CAST(:steps AS jsonb), updated_by = :a, updated_at = now() WHERE tenant_id = :t AND id = :id"), params)
        action = "workflow.updated"
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="workflow", entity_id=wid,
                       after={"name": data["name"], "enabled": data["enabled"],
                              "trigger": f"{data['trigger_type']}:{data.get('trigger_value')}",
                              "steps": [s["type"] for s in data["steps"]]})
    assert wid is not None  # noqa: S101
    return wid


async def delete(conn: AsyncConnection, tenant_id: UUID, wid: UUID, actor: str) -> None:
    await get(conn, tenant_id, wid)
    await conn.execute(text("DELETE FROM workflows WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": wid})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="workflow.deleted", entity_type="workflow", entity_id=wid)


async def runs(conn: AsyncConnection, tenant_id: UUID, wid: UUID | None = None, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT r.id, r.workflow_id, w.name AS workflow_name, r.trigger, r.status, r.input, r.log, r.error, r.queued_at, r.started_at, "
        "r.finished_at FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id WHERE r.tenant_id = :t "
        "AND (CAST(:w AS uuid) IS NULL OR r.workflow_id = CAST(:w AS uuid)) ORDER BY r.queued_at DESC LIMIT :l"),
        {"t": tenant_id, "w": wid, "l": limit})
    return [dict(r) for r in rows.mappings()]


async def enqueue(conn: AsyncConnection, tenant_id: UUID, wid: UUID, trigger: str, payload: dict[str, Any]) -> UUID:
    rid: UUID = await conn.scalar(text(
        "INSERT INTO workflow_runs (tenant_id, workflow_id, trigger, input) VALUES (:t, :w, :tr, CAST(:i AS jsonb)) RETURNING id"),
        {"t": tenant_id, "w": wid, "tr": trigger, "i": json.dumps(payload, default=str)})
    return rid


@events.listen
async def _on_event(conn: AsyncConnection, tenant_id: UUID, event: str, payload: dict[str, Any]) -> None:
    for (wid,) in await conn.execute(text(
            "SELECT id FROM workflows WHERE tenant_id = :t AND enabled AND trigger_type = 'event' AND trigger_value = :e"),
            {"t": tenant_id, "e": event}):
        await enqueue(conn, tenant_id, wid, f"event:{event}", payload)


async def enqueue_cron(conn: AsyncConnection, tenant_id: UUID, now: dt.datetime) -> int:
    """Fällige Cron-Workflows einreihen (höchstens einmal je Minute, Zeitzone Europe/Berlin)."""
    n = 0
    minute = now.replace(second=0, microsecond=0)
    rows = (await conn.execute(text("SELECT id, trigger_value, last_cron_at FROM workflows WHERE tenant_id = :t AND enabled "
                                    "AND trigger_type = 'cron' FOR UPDATE SKIP LOCKED"), {"t": tenant_id})).all()
    for wid, expr, last in rows:
        try:
            due = cron_matches(expr, minute)
        except WorkflowError:
            continue
        if due and (last is None or last.astimezone(minute.tzinfo).replace(second=0, microsecond=0) < minute):
            await enqueue(conn, tenant_id, wid, f"cron:{expr}", {"data": {"time": minute.isoformat()}})
            await conn.execute(text("UPDATE workflows SET last_cron_at = :m WHERE id = :id"), {"m": minute, "id": wid})
            n += 1
    return n


# ───────────────────────── Ausführung ─────────────────────────

async def _python(code: str, data: Any) -> Any:
    s = get_settings()
    if not s.sandbox_url:
        raise WorkflowError("Python-Sandbox ist nicht eingerichtet (Dienst „sandbox“ in docker-compose)")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{s.sandbox_url}/run", content=json.dumps({"code": code, "input": data}, default=str),
                              headers={"X-Sandbox-Token": s.sandbox_token.get_secret_value(), "Content-Type": "application/json"})
    if r.status_code != 200:
        raise WorkflowError(f"Sandbox antwortete {r.status_code}")
    body = r.json()
    if not body.get("ok"):
        raise WorkflowError(f"Python: {body.get('error')}")
    return {"result": body.get("result"), "stdout": body.get("stdout", "")}


async def _run_step(conn: AsyncConnection, ctx_obj: Any, tenant_id: UUID, step: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, Any]:
    """Rückgabe (weiter?, Ergebnis)."""
    kind = step["type"]
    raw = step.get("config") or {}
    cfg = render(raw, ctx) if kind != "python" else raw
    if kind == "condition":
        ok = condition(cfg.get("left"), raw["op"], cfg.get("right"))
        return ok, {"passed": ok}
    if kind == "email":
        from app.services.mail import OutgoingMail

        if ctx_obj.mailer is None:
            raise WorkflowError("E-Mail ist nicht eingerichtet")
        to = [a.strip() for a in str(cfg["to"]).replace(";", ",").split(",") if a.strip()]
        mid = await ctx_obj.mailer.send(OutgoingMail(to=to, subject=str(cfg["subject"])[:200], text=str(cfg["body"])))
        return True, {"message_id": mid, "to": to}
    if kind == "task":
        from app.services import projects

        board = await projects.board(conn, tenant_id, UUID(str(cfg["project_id"])))
        column = board["columns"][0]["id"]
        days = cfg.get("due_in_days")
        due = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=int(days)) if days not in (None, "") else None
        tid = await projects.create_task(conn, tenant_id, UUID(str(cfg["project_id"])), {
            "column_id": column, "title": str(cfg["title"])[:300], "description": str(cfg.get("description") or "") or None,
            "assignee": None, "assignee_id": None, "due_date": due, "priority": cfg.get("priority") or "mittel"}, "workflow")
        return True, {"task_id": str(tid)}
    if kind == "http":
        from app.services.webhooks import check_target

        try:
            await check_target(str(cfg["url"]))
        except ConflictError as exc:
            raise WorkflowError(str(exc)) from exc
        body = cfg.get("body")
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
            content = json.dumps(body, default=str) if isinstance(body, dict | list) else (str(body) if body else None)
            r = await client.request(cfg["method"], str(cfg["url"]), headers=headers, content=content)
        try:
            parsed: Any = r.json()
        except ValueError:
            parsed = r.text[:5000]
        if r.status_code >= 400 and not raw.get("ignore_errors"):
            raise WorkflowError(f"HTTP {r.status_code}: {str(parsed)[:300]}")
        return True, {"status": r.status_code, "body": parsed}
    if kind == "python":
        return True, await _python(raw["code"], {"data": ctx["data"], "steps": ctx["steps"], "event": ctx.get("event")})
    raise WorkflowError("Unbekannter Block")


async def _run_chain(ctx_obj: Any, tenant_id: UUID, steps: list[dict[str, Any]], ctx: dict[str, Any],
                     entries: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Führt eine Kette aus (rekursiv für Abzweigungen). Rückgabe (Status, Fehler)."""
    for step in steps:
        started = dt.datetime.now(dt.UTC)
        try:
            if step["type"] == "if":
                cfg = render({k: v for k, v in (step.get("config") or {}).items() if k not in ("then", "else")}, ctx)
                ok = condition(cfg.get("left"), step["config"]["op"], cfg.get("right"))
                ctx["steps"][step["id"]] = {"passed": ok, "branch": "Ja" if ok else "Nein"}
                entries.append({"id": step["id"], "type": "if", "ok": True, "result": ctx["steps"][step["id"]], "ms": 0})
                status, error = await _run_chain(ctx_obj, tenant_id, step["config"].get("then" if ok else "else") or [], ctx, entries)
                if status != "succeeded":
                    return status, error
                continue
            async with ctx_obj.engine.begin() as conn:
                go_on, result = await _run_step(conn, ctx_obj, tenant_id, step, ctx)
            ctx["steps"][step["id"]] = result
            entries.append({"id": step["id"], "type": step["type"], "ok": True, "result": result,
                            "ms": int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)})
            if not go_on:
                return "stopped", None
        except (WorkflowError, httpx.HTTPError, ConflictError, NotFoundError, ValueError, KeyError) as exc:
            entries.append({"id": step["id"], "type": step["type"], "ok": False, "error": str(exc)[:1000]})
            return "failed", f"{STEP_TYPES.get(step['type'], step['type'])} ({step['id']}): {exc}"[:1000]
    return "succeeded", None


async def execute(ctx_obj: Any, run_id: UUID) -> str:
    """Führt einen eingereihten Lauf aus; jeder Block in eigener Transaktion, Protokoll nach jedem Block."""
    async with ctx_obj.engine.begin() as conn:
        row = (await conn.execute(text(
            "UPDATE workflow_runs r SET status = 'running', started_at = now() FROM workflows w WHERE r.id = :id AND w.id = r.workflow_id "
            "AND r.status = 'queued' RETURNING r.tenant_id, r.input, w.steps, w.name"), {"id": run_id})).one_or_none()
    if row is None:
        return "skipped"
    tenant_id, payload, steps, name = row
    ctx: dict[str, Any] = {"data": payload.get("data", payload), "event": payload.get("event"), "steps": {},
                           "now": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "workflow": name}
    entries: list[dict[str, Any]] = []
    status, error = await _run_chain(ctx_obj, tenant_id, steps, ctx, entries)
    async with ctx_obj.engine.begin() as conn:
        await conn.execute(text("UPDATE workflow_runs SET status = :s, log = CAST(:l AS jsonb), error = :e, finished_at = now() WHERE id "
                                "= :id"),
                           {"s": status, "l": json.dumps(entries, default=str), "e": error, "id": run_id})
    return status


async def process_queue(ctx_obj: Any, now: dt.datetime, limit: int = 20) -> dict[str, int]:
    async with ctx_obj.engine.begin() as conn:
        queued_cron = await enqueue_cron(conn, ctx_obj.tenant_id, now)
        ids = [r[0] for r in await conn.execute(text(
            "SELECT id FROM workflow_runs WHERE status = 'queued' ORDER BY queued_at LIMIT :l"), {"l": limit})]
    stats = {"cron": queued_cron, "succeeded": 0, "stopped": 0, "failed": 0}
    for rid in ids:
        result = await execute(ctx_obj, rid)
        if result in stats:
            stats[result] += 1
    return stats


def _flatten(obj: Any, prefix: str, out: set[str], depth: int = 0) -> None:
    if depth > 3:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.replace("_", "").isalnum():
                path = f"{prefix}.{k}"
                out.add(path)
                _flatten(v, path, out, depth + 1)


async def field_paths(conn: AsyncConnection, tenant_id: UUID, event: str | None) -> list[str]:
    """Verfügbare Felder für Platzhalter: aus den letzten echten Ereignissen gelernt, ergänzt um Grundfelder."""
    paths: set[str] = {"now", "workflow", "event"}
    if event:
        actions = [a for a, (pub, _) in events.EVENTS.items() if pub == event]
        paths.update({"entity_id", "actor"})
        for f in events.FIELD_HINTS.get(event, ()):
            paths.add(f"data.{f}")
        if actions:
            rows = await conn.execute(text(
                "SELECT after FROM audit_log WHERE tenant_id = :t AND action = ANY(:a) AND after IS NOT NULL ORDER BY occurred_at DESC "
                "LIMIT 10"),
                {"t": tenant_id, "a": actions})
            for (after,) in rows:
                _flatten(after, "data", paths)
    else:
        paths.add("data")
    return sorted(paths, key=lambda p: (not p.startswith("data."), p))
