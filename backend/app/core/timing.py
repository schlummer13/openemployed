"""Server-Timing-Header: Verarbeitungszeit jeder Anfrage im Server (sichtbar in den Browser-Entwicklertools).

Reine ASGI-Middleware (ohne Zwischenpuffer), damit sie selbst praktisch nichts kostet. Langsame Anfragen (> 500 ms) werden
protokolliert, um Ausreißer im Betrieb zu finden.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("openemployed.timing")
SLOW_MS = 500.0


class ServerTimingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()

        async def wrapped(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                ms = (time.perf_counter() - start) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"server-timing", f"app;dur={ms:.1f}".encode()))
                message = {**message, "headers": headers}
                if ms > SLOW_MS:
                    log.warning("langsame Anfrage: %s %s %.0f ms", scope.get("method"), scope.get("path"), ms)
            await send(message)

        await self.app(scope, receive, wrapped)
