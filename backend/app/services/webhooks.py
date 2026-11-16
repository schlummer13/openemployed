"""Webhooks: fachliche Ereignisse per HTTPS an eigene Systeme (n8n, Zapier, Make, eigene Server) melden.

Signatur: ``X-OpenEmployed-Signature: t=<Unixzeit>,v1=<HMAC-SHA256(secret, "<t>.<body>")>`` – Empfänger prüfen Signatur und
Zeitstempel (max. 5 Minuten). Zustellung mindestens einmal; bei Fehlern bis zu 8 Wiederholungen mit wachsendem Abstand.
Ziele im internen Netz (localhost, private Adressen) sind in Produktion gesperrt (SSRF-Schutz) – auch nach DNS-Auflösung.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import get_settings
from app.services import audit, events
from app.services.masterdata import ConflictError, NotFoundError

log = logging.getLogger("openemployed.webhooks")
MAX_ATTEMPTS = 8


def _fernet() -> Any:
    from app.services.settings_store import _fernet as f

    return f()


def sign(secret: str, body: bytes, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


async def check_target(url: str) -> None:
    parts = urlsplit(url)
    prod = get_settings().is_production
    if parts.scheme != "https" and (prod or parts.scheme != "http"):
        raise ConflictError("Webhook-Ziel muss mit https:// beginnen")
    if not parts.hostname or parts.username or parts.password:
        raise ConflictError("Webhook-URL ungültig")
    if not prod:
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ConflictError(f"Host {parts.hostname} nicht auflösbar") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ConflictError("Interne Netzadressen sind als Webhook-Ziel nicht erlaubt")


@events.listen
async def _enqueue(conn: AsyncConnection, tenant_id: UUID, event: str, payload: dict[str, Any]) -> None:
    await conn.execute(text(
        "INSERT INTO webhook_deliveries (tenant_id, endpoint_id, event, payload) SELECT :t, id, :e, CAST(:p AS jsonb) FROM "
        "webhook_endpoints "
        "WHERE tenant_id = :t AND active AND (:e = ANY(events) OR '*' = ANY(events))"),
        {"t": tenant_id, "e": event, "p": json.dumps(payload)})


async def endpoints(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT e.id, e.url, e.description, e.events, e.active, e.created_by, e.created_at, "
        "(SELECT count(*) FROM webhook_deliveries d WHERE d.endpoint_id = e.id AND d.status = 'failed') AS failed, "
        "(SELECT max(delivered_at) FROM webhook_deliveries d WHERE d.endpoint_id = e.id) AS last_delivery "
        "FROM webhook_endpoints e WHERE e.tenant_id = :t ORDER BY e.created_at"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def deliveries(conn: AsyncConnection, tenant_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, endpoint_id, event, status, attempts, response_code, last_error, created_at, delivered_at FROM webhook_deliveries "
        "WHERE tenant_id = :t ORDER BY created_at DESC LIMIT :l"), {"t": tenant_id, "l": limit})
    return [dict(r) for r in rows.mappings()]


async def create(conn: AsyncConnection, tenant_id: UUID, url: str, event_names: list[str], description: str | None,
                 actor: str) -> dict[str, Any]:
    bad = [e for e in event_names if e != "*" and e not in events.PUBLIC]
    if bad or not event_names:
        raise ConflictError(f"Unbekannte Ereignisse: {', '.join(bad) or '(keine)'}")
    await check_target(url)
    secret = f"whsec_{secrets.token_urlsafe(24)}"
    wid = await conn.scalar(text(
        "INSERT INTO webhook_endpoints (tenant_id, url, description, events, secret, created_by) VALUES (:t, :u, :d, :e, :s, :a) "
        "RETURNING id"),
        {"t": tenant_id, "u": url, "d": description, "e": event_names, "s": _fernet().encrypt(secret.encode()).decode(), "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="webhook.created", entity_type="webhook", entity_id=wid,
                       after={"url": url, "events": event_names})
    return {"id": wid, "secret": secret}


async def set_active(conn: AsyncConnection, tenant_id: UUID, wid: UUID, active: bool, actor: str) -> None:
    n = (await conn.execute(text("UPDATE webhook_endpoints SET active = :a WHERE tenant_id = :t AND id = :id"),
                            {"t": tenant_id, "id": wid, "a": active})).rowcount
    if not n:
        raise NotFoundError("Webhook nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="webhook.toggled", entity_type="webhook", entity_id=wid,
                       after={"active": active})


async def delete(conn: AsyncConnection, tenant_id: UUID, wid: UUID, actor: str) -> None:
    n = (await conn.execute(text("DELETE FROM webhook_endpoints WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": wid})).rowcount
    if not n:
        raise NotFoundError("Webhook nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="webhook.deleted", entity_type="webhook", entity_id=wid)


async def send_test(conn: AsyncConnection, tenant_id: UUID, wid: UUID) -> None:
    exists = await conn.scalar(text("SELECT 1 FROM webhook_endpoints WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": wid})
    if not exists:
        raise NotFoundError("Webhook nicht gefunden")
    await conn.execute(text("INSERT INTO webhook_deliveries (tenant_id, endpoint_id, event, payload) VALUES (:t, :w, 'ping', CAST(:p AS "
                            "jsonb))"),
                       {"t": tenant_id, "w": wid, "p": json.dumps({"event": "ping", "message": "Testereignis von OpenEmployed"})})


async def deliver_due(engine: AsyncEngine, client: httpx.AsyncClient | None = None, limit: int = 50) -> dict[str, int]:
    """Stellt fällige Ereignisse zu (vom Automatisierungs-Takt jede Minute aufgerufen)."""
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT d.id, d.event, d.payload, d.attempts, e.url, e.secret FROM webhook_deliveries d JOIN webhook_endpoints e "
            "ON e.id = d.endpoint_id WHERE d.status = 'pending' AND d.next_attempt <= now() AND e.active "
            "ORDER BY d.next_attempt LIMIT :l FOR UPDATE OF d SKIP LOCKED"), {"l": limit})).mappings().all()
        if not rows:
            return {"delivered": 0, "failed": 0}
        f = _fernet()
        stats = {"delivered": 0, "failed": 0}
        own = client is None
        http = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False)
        try:
            for r in rows:
                body = json.dumps({**r["payload"], "delivery_id": str(r["id"])}, separators=(",", ":")).encode()
                code, error = None, None
                try:
                    await check_target(r["url"])
                    secret = f.decrypt(r["secret"].encode()).decode()
                    resp = await http.post(r["url"], content=body, headers={
                        "Content-Type": "application/json", "User-Agent": "openemployed-webhooks/1.0", "X-OpenEmployed-Event": r["event"],
                        "X-OpenEmployed-Delivery": str(r["id"]), "X-OpenEmployed-Signature": sign(secret, body, int(time.time()))})
                    code = resp.status_code
                    if 200 <= code < 300:
                        await conn.execute(text("UPDATE webhook_deliveries SET status = 'delivered', attempts = attempts + 1, "
                                                "response_code = :c, delivered_at = now(), last_error = NULL WHERE id = :id"),
                                           {"c": code, "id": r["id"]})
                        stats["delivered"] += 1
                        continue
                    error = f"HTTP {code}"
                except (httpx.HTTPError, ConflictError) as exc:
                    error = f"{type(exc).__name__}: {exc}"[:500]
                attempts = r["attempts"] + 1
                failed = attempts >= MAX_ATTEMPTS
                await conn.execute(text(
                    "UPDATE webhook_deliveries SET attempts = :n, response_code = :c, last_error = :e, status = :s, "
                    "next_attempt = now() + make_interval(mins => :m) WHERE id = :id"),
                    {"n": attempts, "c": code, "e": error, "s": "failed" if failed else "pending", "m": 2 ** attempts, "id": r["id"]})
                if failed:
                    stats["failed"] += 1
        finally:
            if own:
                await http.aclose()
    return stats
