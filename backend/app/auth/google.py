"""Anmeldung mit Google Workspace (OpenID Connect, Authorization Code Flow mit PKCE).

Keine Rollen: Wer alle Prüfungen besteht, hat vollen Zugriff.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 – URL, kein Passwort
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class AuthError(Exception):
    """Anmeldung abgelehnt. ``code`` geht an den Browser, ``reason`` nur ins Log."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


@dataclass(frozen=True, slots=True)
class PkcePair:
    verifier: str
    challenge: str


def new_pkce() -> PkcePair:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return PkcePair(verifier, challenge)


def authorization_url(*, client_id: str, redirect_uri: str, state: str, nonce: str, code_challenge: str,
                      hosted_domain: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "hd": hosted_domain,          # nur Hinweis an Google – geprüft wird serverseitig im ID-Token
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def validate_claims(
    claims: dict[str, Any],
    *,
    client_id: str,
    allowed_domain: str,
    allowed_emails: frozenset[str],
    expected_nonce: str,
) -> GoogleIdentity:
    """Fachliche Prüfung des (bereits signaturgeprüften) ID-Tokens."""
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("invalid_token", f"falscher Aussteller: {claims.get('iss')!r}")
    if claims.get("aud") != client_id:
        raise AuthError("invalid_token", "Token ist nicht für diese Anwendung ausgestellt")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not hmac.compare_digest(nonce, expected_nonce):
        raise AuthError("invalid_token", "nonce stimmt nicht")
    if claims.get("email_verified") is not True:
        raise AuthError("email_not_verified", "E-Mail-Adresse nicht verifiziert")
    email = str(claims.get("email", "")).strip().lower()
    domain = allowed_domain.lower()
    if not domain or claims.get("hd") != domain or not email.endswith("@" + domain):
        raise AuthError("wrong_domain", f"Konto {email!r} gehört nicht zur Workspace-Domain {domain!r}")
    if allowed_emails and email not in allowed_emails:
        raise AuthError("not_allowed", f"{email!r} steht nicht auf der Freigabeliste")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("invalid_token", "sub fehlt")
    name = claims.get("name")
    picture = claims.get("picture")
    return GoogleIdentity(
        sub=sub,
        email=email,
        name=name if isinstance(name, str) else None,
        picture=picture if isinstance(picture, str) and picture.startswith("https://") else None,
    )


class GoogleOAuth(Protocol):
    async def exchange_code(self, code: str, code_verifier: str) -> str:
        """Tauscht den Code gegen Token und gibt das ID-Token zurück."""
        ...

    async def verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Prüft Signatur, Aussteller, Zielgruppe und Ablauf; gibt die Claims zurück."""
        ...


class GoogleOAuthClient:
    def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str, timeout: float = 10.0) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._timeout = timeout

    async def exchange_code(self, code: str, code_verifier: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
        if resp.status_code != 200:
            raise AuthError("token_exchange_failed", f"Google-Token-Endpunkt antwortete {resp.status_code}")
        id_token = resp.json().get("id_token")
        if not isinstance(id_token, str):
            raise AuthError("token_exchange_failed", "kein id_token in der Antwort")
        return id_token

    async def verify_id_token(self, id_token: str) -> dict[str, Any]:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        def _verify() -> dict[str, Any]:
            claims: dict[str, Any] = google_id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                id_token, google_requests.Request(), audience=self._client_id, clock_skew_in_seconds=10
            )
            return claims

        try:
            return await asyncio.to_thread(_verify)
        except ValueError as exc:
            raise AuthError("invalid_token", f"ID-Token ungültig: {exc}") from exc


def safe_next_path(value: str | None) -> str:
    """Nur relative Pfade innerhalb der App zulassen (Schutz vor offenen Weiterleitungen)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or len(value) > 500:
        return "/"
    if any(ord(ch) < 32 for ch in value):
        return "/"
    return value
