from __future__ import annotations

import hmac
import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import signed_cookie
from app.auth.deps import CurrentUser
from app.auth.google import (
    AuthError,
    GoogleIdentity,
    GoogleOAuth,
    authorization_url,
    new_pkce,
    safe_next_path,
    validate_claims,
)
from app.auth.store import AuthStore
from app.core.config import get_settings

log = logging.getLogger("selbstlite.auth")
router = APIRouter(prefix="/api/auth", tags=["anmeldung"])

OAUTH_COOKIE_MAX_AGE = 600


def _client_ip(request: Request) -> str | None:
    # nginx setzt X-Forwarded-For = $remote_addr; uvicorn läuft mit --proxy-headers → request.client ist bereits die echte IP
    return request.client.host if request.client else None


def _login_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={code}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "google_configured": s.google_login_configured,
        "microsoft_configured": s.microsoft_login_configured,
        "oidc_configured": s.oidc_login_configured,
        "oidc_label": s.auth_oidc_label,
        "password_enabled": s.auth_password_enabled,
        "domain": s.auth_allowed_domain or None,
        "dev_login": s.dev_login_enabled and not s.is_production,
    }


class DevLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = Field(default=None, max_length=100)


@router.post("/dev-login", include_in_schema=False)
async def dev_login(request: Request, data: DevLoginIn) -> Response:
    """NUR ENTWICKLUNG: Anmeldung ohne Google für lokale Browsertests. In Produktion nicht vorhanden (404)."""
    s = get_settings()
    if not s.dev_login_enabled or s.is_production:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    email = data.email.strip().lower()
    if s.auth_allowed_domain and not email.endswith("@" + s.auth_allowed_domain):
        return JSONResponse({"detail": f"Nur Adressen @{s.auth_allowed_domain}"}, status_code=403)
    store: AuthStore = request.app.state.auth_store
    user = await store.upsert_google_user(GoogleIdentity(sub=f"dev:{email}", email=email, name=data.name, picture=None))
    if user is None:
        return JSONResponse({"detail": "Benutzer gesperrt"}, status_code=403)
    token = await store.create_session(user, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    log.warning("ENTWICKLER-ANMELDUNG genutzt: %s", email)
    response = JSONResponse({"email": user.email})
    response.set_cookie(s.session_cookie_name, token, max_age=s.session_max_age_hours * 3600, httponly=True,
                        secure=s.cookie_secure, samesite="lax", path="/")
    return response


@router.get("/google/login")
async def google_login(next: Annotated[str | None, Query(max_length=500)] = None) -> Response:
    s = get_settings()
    if not s.google_login_configured:
        return JSONResponse({"detail": "Google-Anmeldung ist nicht eingerichtet"}, status_code=503)
    state, nonce, pkce = secrets.token_urlsafe(32), secrets.token_urlsafe(32), new_pkce()
    url = authorization_url(
        client_id=s.google_client_id,
        redirect_uri=s.google_redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=pkce.challenge,
        hosted_domain=s.auth_allowed_domain,
    )
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        s.oauth_cookie_name,
        signed_cookie.sign(
            {"state": state, "nonce": nonce, "verifier": pkce.verifier, "next": safe_next_path(next)},
            s.session_secret.get_secret_value(),
            max_age_seconds=OAUTH_COOKIE_MAX_AGE,
        ),
        max_age=OAUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",  # nötig: Google leitet per Top-Level-GET zurück
        path="/",
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=200)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    s = get_settings()
    saved = signed_cookie.verify(request.cookies.get(s.oauth_cookie_name), s.session_secret.get_secret_value())

    def finish(resp: Response) -> Response:
        resp.delete_cookie(s.oauth_cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")
        return resp

    if error:
        log.info("Google-Anmeldung abgebrochen: %s", error)
        return finish(_login_error("cancelled"))
    if not saved or not code or not state or not hmac.compare_digest(str(saved.get("state", "")), state):
        log.warning("Google-Anmeldung: state ungültig oder abgelaufen")
        return finish(_login_error("expired"))

    google: GoogleOAuth = request.app.state.google
    store: AuthStore = request.app.state.auth_store
    try:
        id_token = await google.exchange_code(code, str(saved["verifier"]))
        claims = await google.verify_id_token(id_token)
        identity = validate_claims(
            claims,
            client_id=s.google_client_id,
            allowed_domain=s.auth_allowed_domain,
            allowed_emails=s.allowed_emails,
            expected_nonce=str(saved["nonce"]),
        )
    except AuthError as exc:
        log.warning("Google-Anmeldung abgelehnt (%s): %s", exc.code, exc.reason)
        return finish(_login_error(exc.code))

    user = await store.upsert_google_user(identity)
    if user is None:
        log.warning("Google-Anmeldung: Benutzer %s ist gesperrt", identity.email)
        return finish(_login_error("disabled"))

    token = await store.create_session(user, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    response = finish(RedirectResponse(safe_next_path(str(saved.get("next") or "/")), status_code=303))
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.session_max_age_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
    log.info("Anmeldung: %s", user.email)
    return response


@router.get("/me")
async def me(request: Request, user: CurrentUser) -> dict[str, Any]:
    from sqlalchemy import text

    from app.auth.roles import ROLE_LABELS

    out: dict[str, Any] = {"id": str(user.id), "email": user.email, "name": user.name, "picture": user.picture, "role": user.role,
                           "role_label": ROLE_LABELS.get(user.role, user.role), "permissions": dict(user.permissions)}
    engine = getattr(request.app.state, "engine", None)
    if engine is not None and not user.via_api_key:
        try:
            async with engine.connect() as conn:
                r = (await conn.execute(text("SELECT auth_provider, totp_enabled, password_hash IS NOT NULL AS has_password FROM users "
                                             "WHERE id = :id"), {"id": user.id})).one_or_none()
        except Exception:  # Zusatzangaben sind optional (z. B. Tests ohne Datenbank)
            r = None
        if r is not None and user.role not in ROLE_LABELS:
            from app.services.roles import label_of

            async with engine.connect() as conn:
                out["role_label"] = await label_of(conn, user.role)
        if r is not None:
            out |= {"auth_provider": r.auth_provider, "totp_enabled": r.totp_enabled, "has_password": r.has_password,
                    "needs_2fa": get_settings().auth_require_2fa and r.auth_provider == "password" and not r.totp_enabled}
    return out


def _session_response(body: dict[str, Any], token: str) -> Response:
    s = get_settings()
    response = JSONResponse(body)
    response.set_cookie(s.session_cookie_name, token, max_age=s.session_max_age_hours * 3600, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    return response


# ───────────────────────── Microsoft Entra ID und OpenID Connect ─────────────────────────

def _oidc_client(provider: str) -> Any:
    from app.auth.oidc import MICROSOFT_DISCOVERY, OidcClient, OidcConfig

    s = get_settings()
    if provider == "microsoft" and s.microsoft_login_configured:
        return OidcClient(OidcConfig("microsoft", MICROSOFT_DISCOVERY.format(tenant=s.auth_microsoft_tenant), s.auth_microsoft_client_id,
                                     s.auth_microsoft_client_secret.get_secret_value(), s.oidc_redirect_uri("microsoft"), "Microsoft",
                                     s.auth_allowed_domain))
    if provider == "oidc" and s.oidc_login_configured:
        return OidcClient(OidcConfig("oidc", s.auth_oidc_discovery_url, s.auth_oidc_client_id, s.auth_oidc_client_secret.get_secret_value(),
                                     s.oidc_redirect_uri("oidc"), s.auth_oidc_label, s.auth_allowed_domain))
    return None


@router.get("/{provider}/login")
async def oidc_login(provider: str, next: Annotated[str | None, Query(max_length=500)] = None) -> Response:
    if provider not in ("microsoft", "oidc"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    client = _oidc_client(provider)
    if client is None:
        return JSONResponse({"detail": "Anmeldeverfahren ist nicht eingerichtet"}, status_code=503)
    s = get_settings()
    state, nonce, pkce = secrets.token_urlsafe(32), secrets.token_urlsafe(32), new_pkce()
    try:
        url = await client.authorization_url(state=state, nonce=nonce, code_challenge=pkce.challenge)
    except AuthError as exc:
        log.warning("OIDC (%s) nicht erreichbar: %s", provider, exc.reason)
        return _login_error("provider_unavailable")
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(s.oauth_cookie_name, signed_cookie.sign(
        {"state": state, "nonce": nonce, "verifier": pkce.verifier, "next": safe_next_path(next), "provider": provider},
        s.session_secret.get_secret_value(), max_age_seconds=OAUTH_COOKIE_MAX_AGE),
        max_age=OAUTH_COOKIE_MAX_AGE, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    return response


@router.get("/{provider}/callback")
async def oidc_callback(request: Request, provider: str, code: Annotated[str | None, Query(max_length=4096)] = None,
                        state: Annotated[str | None, Query(max_length=200)] = None,
                        error: Annotated[str | None, Query(max_length=200)] = None) -> Response:
    from app.auth.oidc import identity_from_claims

    if provider not in ("microsoft", "oidc"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    s = get_settings()
    saved = signed_cookie.verify(request.cookies.get(s.oauth_cookie_name), s.session_secret.get_secret_value())

    def finish(resp: Response) -> Response:
        resp.delete_cookie(s.oauth_cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")
        return resp

    if error:
        return finish(_login_error("cancelled"))
    client = _oidc_client(provider)
    if (client is None or not saved or saved.get("provider") != provider or not code or not state
            or not hmac.compare_digest(str(saved.get("state", "")), state)):
        return finish(_login_error("expired"))
    try:
        claims = await client.verify(await client.exchange_code(code, str(saved["verifier"])), nonce=str(saved["nonce"]))
        sub, email, name = identity_from_claims(provider, claims, s.auth_allowed_domain)
    except AuthError as exc:
        log.warning("%s-Anmeldung abgelehnt (%s): %s", provider, exc.code, exc.reason)
        return finish(_login_error(exc.code))
    if s.allowed_emails and email not in s.allowed_emails:
        return finish(_login_error("not_allowed"))
    store: AuthStore = request.app.state.auth_store
    user = await store.upsert_identity(provider, sub, email, name, None)
    if user is None:
        return finish(_login_error("disabled"))
    token = await store.create_session(user, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    response = finish(RedirectResponse(safe_next_path(str(saved.get("next") or "/")), status_code=303))
    response.set_cookie(s.session_cookie_name, token, max_age=s.session_max_age_hours * 3600, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    return response


# ───────────────────────── E-Mail + Passwort (+ TOTP) ─────────────────────────

class PasswordLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    totp: str | None = Field(default=None, max_length=10)


MESSAGES = {"invalid": "E-Mail oder Passwort falsch", "locked": "Zu viele Fehlversuche – Konto für 15 Minuten gesperrt",
            "totp_invalid": "Zwei-Faktor-Code ungültig"}


@router.post("/password/login")
async def password_login(request: Request, data: PasswordLoginIn) -> Response:
    s = get_settings()
    if not s.auth_password_enabled:
        return JSONResponse({"detail": "Passwort-Anmeldung ist nicht aktiviert"}, status_code=403)
    store: AuthStore = request.app.state.auth_store
    result, user = await store.password_login(data.email.strip().lower(), data.password, data.totp)
    if result == "totp_required":
        return JSONResponse({"status": "totp_required"})
    if user is None:
        return JSONResponse({"detail": MESSAGES.get(result, "Anmeldung fehlgeschlagen")}, status_code=401)
    token = await store.create_session(user, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    return _session_response({"status": result}, token)


@router.get("/invite/{token}")
async def invite_info(request: Request, token: str) -> Any:
    from app.api.core_routes import run, tenant
    from app.services import users

    s = get_settings()
    info = await run(request, lambda conn: users.invite_info(conn, tenant(), token))
    return {"email": info["email"], "name": info["name"], "role_label": info["role_label"], "password_allowed": True,
            "google": s.google_login_configured, "microsoft": s.microsoft_login_configured, "oidc": s.oidc_login_configured}


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=200)


@router.post("/invite/{token}")
async def accept_invite(request: Request, token: str, data: AcceptIn) -> Response:
    from app.api.core_routes import run, tenant
    from app.services import users

    uid = await run(request, lambda conn: users.accept_invite(conn, tenant(), token, data.password))
    store: AuthStore = request.app.state.auth_store
    user = await store.get_user(uid)
    if user is None:
        return JSONResponse({"detail": "Benutzer gesperrt"}, status_code=403)
    token_ = await store.create_session(user, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    return _session_response({"status": "ok"}, token_)


class PasswordChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str | None = Field(default=None, max_length=200)
    new: str = Field(min_length=12, max_length=200)


@router.post("/password", status_code=204)
async def change_password(request: Request, data: PasswordChangeIn, user: CurrentUser) -> None:
    from app.api.core_routes import run, tenant
    from app.services import users

    await run(request, lambda conn: users.change_password(conn, user.id, data.current, data.new, user.email, tenant()))


@router.post("/totp/start")
async def totp_start(request: Request, user: CurrentUser) -> Any:
    from app.api.core_routes import run
    from app.services import users

    return await run(request, lambda conn: users.start_totp(conn, user.id, user.email, "OpenEmployed"))


class TotpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=10)


@router.post("/totp/confirm", status_code=204)
async def totp_confirm(request: Request, data: TotpIn, user: CurrentUser) -> None:
    from app.api.core_routes import run, tenant
    from app.services import users

    await run(request, lambda conn: users.confirm_totp(conn, user.id, data.code, user.email, tenant()))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    s = get_settings()
    token = request.cookies.get(s.session_cookie_name)
    if token:
        store: AuthStore = request.app.state.auth_store
        user = await store.resolve_session(token)
        await store.revoke_session(token, actor=user.email if user else None)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(s.session_cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")
    return response
