from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.store import AuthStore, User
from app.core.config import get_settings


async def _api_key_user(request: Request, key: str) -> User | None:
    from app.services.api_keys import resolve

    engine = getattr(request.app.state, "engine", None)
    return await resolve(engine, key) if engine is not None else None


async def _apply_company(request: Request, user: User) -> User:
    """Gewählte Firma (Cookie) übernehmen, wenn der Benutzer dort Zugriff hat; Rolle gilt je Firma."""
    import dataclasses
    from uuid import UUID

    from app.core import tenancy

    tenancy.set_current(None)
    raw = request.cookies.get(tenancy.COOKIE)
    engine = getattr(request.app.state, "engine", None)
    if not raw or engine is None or user.via_api_key:
        return user
    try:
        company = UUID(raw)
    except ValueError:
        return user
    from sqlalchemy import text

    from app.services.companies import role_in

    try:
        async with engine.connect() as conn:
            home = await conn.scalar(text("SELECT tenant_id FROM users WHERE id = :u"), {"u": user.id})
            if home is None:
                return user
            role = await role_in(conn, user.id, home, user.role, company)
    except Exception:  # z. B. Tests ohne Datenbank
        return user
    if role is None:
        return user
    tenancy.set_current(company)
    return dataclasses.replace(user, role=role)


async def current_user(request: Request) -> User:
    """Angemeldeter Benutzer (Sitzungs-Cookie oder API-Schlüssel) – sonst 401; Rechte nach Rolle – sonst 403."""
    import dataclasses

    from app.auth.roles import allowed_with
    from app.services.roles import resolve

    user: User | None = None
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and auth[7:].startswith("sl_"):
        user = await _api_key_user(request, auth[7:].strip())
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API-Schlüssel ungültig oder widerrufen")
    else:
        token = request.cookies.get(get_settings().session_cookie_name)
        if token:
            store: AuthStore = request.app.state.auth_store
            user = await store.resolve_session(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht angemeldet")
    user = await _apply_company(request, user)
    perms = await resolve(getattr(request.app.state, "engine", None), user.role)
    user = dataclasses.replace(user, permissions=tuple(sorted(perms.items())))
    if not allowed_with(perms, request.method, request.url.path):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Keine Berechtigung für diese Funktion (Rolle)")
    return user


CurrentUser = Annotated[User, Depends(current_user)]

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_PREFIXES = ("/api/webhooks/",)


class OriginCheckMiddleware:
    """CSRF-Schutz: schreibende API-Anfragen nur von der eigenen App-Herkunft.

    Prüft den Origin-Header, ersatzweise den Referer. Webhooks sind ausgenommen (eigene Signaturprüfung).
    """

    def __init__(self, app: ASGIApp, *, allowed_origin: str, extra_origins: tuple[str, ...] = ()) -> None:
        self.app = app
        self.allowed_origin = allowed_origin
        self.origins = frozenset({allowed_origin, *(o for o in extra_origins if o)})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            path: str = scope["path"]
            if path.startswith("/api/") and not path.startswith(CSRF_EXEMPT_PREFIXES):
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
                # API-Schlüssel (Bearer) ohne Cookie: kein CSRF-Risiko, Herkunftsprüfung entfällt
                api_key = headers.get("authorization", "")[:10].lower() == "bearer sl_" and "cookie" not in headers
                if not api_key and not self._allowed(headers.get("origin"), headers.get("referer")):
                    await _forbidden(send)
                    return
        await self.app(scope, receive, send)

    def _allowed(self, origin: str | None, referer: str | None) -> bool:
        if origin is not None:
            return origin in self.origins
        if referer:
            parts = urlsplit(referer)
            return f"{parts.scheme}://{parts.netloc}" in self.origins
        return False


async def _forbidden(send: Send) -> None:
    body = b'{"detail":"Anfrage von fremder Herkunft abgelehnt"}'
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
