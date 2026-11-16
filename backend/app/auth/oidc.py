"""OpenID Connect (Authorization Code + PKCE) für Microsoft Entra ID und beliebige Anbieter (Keycloak, Authentik, Zitadel, Nextcloud …).

ID-Token werden gegen die JWKS des Anbieters geprüft (RS256/RS384/RS512/ES256), dazu Aussteller, Zielgruppe, Ablauf und Nonce.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from app.auth.google import AuthError

MICROSOFT_DISCOVERY = "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"


@dataclass(frozen=True, slots=True)
class OidcConfig:
    provider: str          # "microsoft" | "oidc"
    discovery_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    label: str = "Single Sign-on"
    allowed_domain: str = ""


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class OidcClient:
    _cache: ClassVar[dict[str, tuple[float, dict[str, Any]]]] = {}

    def __init__(self, cfg: OidcConfig, timeout: float = 10.0) -> None:
        self.cfg = cfg
        self._timeout = timeout

    async def _get_json(self, url: str, ttl: float = 3600) -> dict[str, Any]:
        hit = self._cache.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            r = await client.get(url)
        if r.status_code != 200:
            raise AuthError("provider_unavailable", f"{url} antwortete {r.status_code}")
        data: dict[str, Any] = r.json()
        self._cache[url] = (time.time() + ttl, data)
        return data

    async def metadata(self) -> dict[str, Any]:
        meta = await self._get_json(self.cfg.discovery_url)
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if not str(meta.get(key, "")).startswith("https://") and key != "issuer":
                raise AuthError("provider_unavailable", f"Discovery ohne {key} (https erforderlich)")
        return meta

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        meta = await self.metadata()
        params = {"client_id": self.cfg.client_id, "response_type": "code", "redirect_uri": self.cfg.redirect_uri,
                  "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": code_challenge,
                  "code_challenge_method": "S256", "prompt": "select_account"}
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, code: str, verifier: str) -> str:
        meta = await self.metadata()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(meta["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.cfg.redirect_uri, "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret, "code_verifier": verifier})
        if r.status_code != 200:
            raise AuthError("token_exchange_failed", f"Token-Endpunkt antwortete {r.status_code}")
        token = r.json().get("id_token")
        if not isinstance(token, str):
            raise AuthError("token_exchange_failed", "kein id_token")
        return token

    async def verify(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        meta = await self.metadata()
        try:
            head_b64, payload_b64, sig_b64 = id_token.split(".")
            header = json.loads(_b64url(head_b64))
            claims: dict[str, Any] = json.loads(_b64url(payload_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthError("invalid_token", "ID-Token nicht lesbar") from exc
        jwks = await self._get_json(meta["jwks_uri"])
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
        if key is None:  # Schlüsselrotation: einmal frisch laden
            self._cache.pop(meta["jwks_uri"], None)
            jwks = await self._get_json(meta["jwks_uri"])
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
        if key is None:
            raise AuthError("invalid_token", "Signaturschlüssel unbekannt")
        _verify_signature(header.get("alg", ""), key, f"{head_b64}.{payload_b64}".encode(), _b64url(sig_b64))
        issuer = str(meta["issuer"])
        if "{tenantid}" in issuer:  # Microsoft „common/organizations“: Aussteller enthält die Mandanten-ID
            issuer = issuer.replace("{tenantid}", str(claims.get("tid", "")))
        now = time.time()
        aud = claims.get("aud")
        if claims.get("iss") != issuer:
            raise AuthError("invalid_token", "Aussteller stimmt nicht")
        if (aud if isinstance(aud, list) else [aud]).count(self.cfg.client_id) == 0:
            raise AuthError("invalid_token", "Zielgruppe stimmt nicht")
        if float(claims.get("exp", 0)) < now - 30 or float(claims.get("iat", now)) > now + 300:
            raise AuthError("invalid_token", "Token abgelaufen")
        if claims.get("nonce") != nonce:
            raise AuthError("invalid_token", "Nonce stimmt nicht")
        return claims


def _verify_signature(alg: str, jwk: dict[str, Any], signing_input: bytes, signature: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    hashes_by_alg = {"RS256": hashes.SHA256(), "RS384": hashes.SHA384(), "RS512": hashes.SHA512(), "ES256": hashes.SHA256()}
    if alg not in hashes_by_alg:
        raise AuthError("invalid_token", f"Signaturverfahren {alg} nicht unterstützt")
    try:
        if alg.startswith("RS"):
            pub = rsa.RSAPublicNumbers(int.from_bytes(_b64url(jwk["e"]), "big"), int.from_bytes(_b64url(jwk["n"]), "big")).public_key()
            pub.verify(signature, signing_input, padding.PKCS1v15(), hashes_by_alg[alg])
        else:
            curve = ec.SECP256R1()
            pub_ec = ec.EllipticCurvePublicNumbers(int.from_bytes(_b64url(jwk["x"]), "big"), int.from_bytes(_b64url(jwk["y"]), "big"),
                                                   curve).public_key()
            half = len(signature) // 2
            der = encode_dss_signature(int.from_bytes(signature[:half], "big"), int.from_bytes(signature[half:], "big"))
            pub_ec.verify(der, signing_input, ec.ECDSA(hashes_by_alg[alg]))
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise AuthError("invalid_token", "Signatur ungültig") from exc


def identity_from_claims(provider: str, claims: dict[str, Any], allowed_domain: str = "") -> tuple[str, str, str | None]:
    """(stabile Kennung, E-Mail, Name). Microsoft: Objekt-ID + Mandant statt der veränderlichen E-Mail."""
    email = str(claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    if "@" not in email:
        raise AuthError("no_email", "Der Anbieter liefert keine E-Mail-Adresse")
    if provider == "oidc" and claims.get("email_verified") is False:
        raise AuthError("email_unverified", "E-Mail-Adresse beim Anbieter nicht bestätigt")
    if allowed_domain and not email.endswith("@" + allowed_domain):
        raise AuthError("wrong_domain", f"Nur Adressen @{allowed_domain}")
    sub = f"{provider}:{claims.get('tid', claims.get('iss', ''))}:{claims.get('oid') or claims.get('sub')}"
    name = claims.get("name")
    return sub, email, name if isinstance(name, str) else None
