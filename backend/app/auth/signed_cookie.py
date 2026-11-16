"""Kurzlebige, signierte Cookie-Werte (HMAC-SHA-256) für den OAuth-Zwischenzustand.

Format: base64url(JSON) "." base64url(HMAC). Enthält ein Ablaufdatum; Manipulation oder Ablauf → None.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, body: str) -> str:
    return _b64e(hmac.new(secret, b"selbstlite-signed-cookie:" + body.encode("ascii"), hashlib.sha256).digest())


def sign(data: dict[str, Any], secret: str, *, max_age_seconds: int, now: float | None = None) -> str:
    payload = {**data, "exp": int((now if now is not None else time.time()) + max_age_seconds)}
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_mac(secret.encode('utf-8'), body)}"


def verify(token: str | None, secret: str, *, now: float | None = None) -> dict[str, Any] | None:
    if not token or token.count(".") != 1 or len(token) > 4096:
        return None
    body, mac = token.split(".")
    if not hmac.compare_digest(mac, _mac(secret.encode("utf-8"), body)):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
        return None
    if payload["exp"] < (now if now is not None else time.time()):
        return None
    payload.pop("exp")
    return payload
