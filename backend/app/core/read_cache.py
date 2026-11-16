"""Lese-Zwischenspeicher für aufwendige Übersichten (Dashboard, „Zu erledigen“).

Gültigkeit: Ein Eintrag gilt nur, solange sich weder die höchste Audit-ID (GoBD-Protokoll, auch Hintergrundjobs) noch der
gemeinsame Änderungszähler ``read_cache_version_seq`` geändert hat. Den Zähler erhöht :class:`InvalidateOnWriteMiddleware`
bei **jeder** schreibenden API-Anfrage, bevor die Antwort rausgeht – auch bei Änderungen ohne Audit-Eintrag und über mehrere
Server-Prozesse hinweg. Zusätzlich verfällt jeder Eintrag nach ``ttl`` Sekunden (zeitabhängige Werte wie „überfällig“,
Ledger-Abfragen) und das Tagesdatum ist Teil des Schlüssels. Kosten der Prüfung: < 0,1 ms.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_store: dict[tuple[Any, ...], tuple[tuple[int, int], float, Any]] = {}
_MAX = 512
stats = {"hits": 0, "misses": 0}


async def cached(conn: AsyncConnection, key: tuple[Any, ...], compute: Callable[[], Awaitable[Any]], *, ttl: float = 30.0) -> Any:
    row = (await conn.execute(text(
        "SELECT (SELECT coalesce(max(id), 0) FROM audit_log), (SELECT last_value FROM read_cache_version_seq)"))).one()
    version = (int(row[0]), int(row[1]))
    key = (conn.engine.url.database, *key)  # nie Einträge verschiedener Datenbanken verwechseln
    now = time.monotonic()
    hit = _store.get(key)
    if hit and hit[0] == version and now - hit[1] < ttl:
        stats["hits"] += 1
        return hit[2]
    stats["misses"] += 1
    value = await compute()
    if len(_store) >= _MAX:
        _store.pop(next(iter(_store)))
    _store[key] = (version, now, value)
    return value


def clear() -> None:
    _store.clear()


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class InvalidateOnWriteMiddleware:
    """Zählt nach jeder schreibenden API-Anfrage den gemeinsamen Änderungszähler hoch – vor dem Versand der Antwort, damit eine
    unmittelbar folgende Leseanfrage (auch an einen anderen Prozess) nie veraltete Daten sieht."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") not in _WRITE_METHODS or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def wrapped(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                engine = getattr(scope["app"].state, "engine", None)
                if engine is not None:
                    try:
                        async with engine.connect() as conn:
                            await conn.execute(text("SELECT nextval('read_cache_version_seq')"))
                    except Exception:  # Zähler nicht erreichbar → lokalen Speicher leeren, TTL greift für andere Prozesse
                        clear()
                else:
                    clear()
            await send(message)

        await self.app(scope, receive, wrapped)
