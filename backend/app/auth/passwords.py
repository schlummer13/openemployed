"""Passwörter (scrypt, RFC 7914) und Zwei-Faktor-Codes (TOTP, RFC 6238) – nur Standardbibliothek."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

SCRYPT = {"n": 2**15, "r": 8, "p": 1, "dklen": 32, "maxmem": 64 * 1024 * 1024}
MIN_PASSWORD = 12


def password_problem(password: str, email: str = "") -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Mindestens {MIN_PASSWORD} Zeichen"
    if len(password) > 200:
        return "Höchstens 200 Zeichen"
    if email and email.split("@")[0].lower() in password.lower():
        return "Das Passwort darf die E-Mail-Adresse nicht enthalten"
    if len(set(password)) < 6:
        return "Zu einfach – bitte mehr unterschiedliche Zeichen"
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        # gleiche Rechenzeit auch ohne Benutzer (kein Rückschluss auf existierende Konten)
        hashlib.scrypt(b"x", salt=b"0" * 16, **SCRYPT)
        return False
    _, n, r, p, salt, dk = stored.split("$")
    test = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=SCRYPT["maxmem"])
    return hmac.compare_digest(test, base64.b64decode(dk))


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return f"{code:0{digits}d}"


def verify_totp(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    code = code.replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = at if at is not None else time.time()
    return any(hmac.compare_digest(totp(secret, now + i * 30), code) for i in range(-window, window + 1))


def otpauth_uri(secret: str, email: str, issuer: str) -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def qr_svg_data_uri(content: str) -> str:
    import io

    import segno

    buf = io.BytesIO()
    segno.make(content, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111827")
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
