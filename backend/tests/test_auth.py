from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.auth import signed_cookie
from app.auth.deps import OriginCheckMiddleware
from app.auth.google import AuthError, new_pkce, safe_next_path, validate_claims
from app.auth.store import MemoryAuthStore
from tests.conftest import CLIENT_ID, FakeGoogle, good_claims

# ───────────────────────── reine Funktionen ─────────────────────────


def _validate(claims: dict[str, Any], *, emails: frozenset[str] = frozenset()) -> Any:
    return validate_claims(claims, client_id=CLIENT_ID, allowed_domain="beispiel.de", allowed_emails=emails,
                           expected_nonce="n1")


def test_valid_claims() -> None:
    identity = _validate(good_claims(nonce="n1", email="Anna@Beispiel.de"))
    assert identity.email == "anna@beispiel.de"
    assert identity.sub == "google-sub-1"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"iss": "https://evil.example"}, "invalid_token"),
        ({"aud": "andere-app"}, "invalid_token"),
        ({"nonce": "falsch"}, "invalid_token"),
        ({"email_verified": False}, "email_not_verified"),
        ({"hd": None, "email": "anna@gmail.com"}, "wrong_domain"),           # privates Gmail-Konto
        ({"hd": "andere.de", "email": "anna@andere.de"}, "wrong_domain"),
        ({"hd": "beispiel.de", "email": "anna@evil.de"}, "wrong_domain"),     # hd passt, Adresse nicht
        ({"hd": "beispiel.de.evil.com", "email": "a@beispiel.de.evil.com"}, "wrong_domain"),
        ({"sub": ""}, "invalid_token"),
    ],
)
def test_rejected_claims(overrides: dict[str, Any], code: str) -> None:
    claims = good_claims(nonce="n1")
    claims.update(overrides)
    with pytest.raises(AuthError) as exc:
        _validate(claims)
    assert exc.value.code == code


def test_allow_list() -> None:
    with pytest.raises(AuthError) as exc:
        _validate(good_claims(nonce="n1"), emails=frozenset({"bob@beispiel.de"}))
    assert exc.value.code == "not_allowed"
    assert _validate(good_claims(nonce="n1"), emails=frozenset({"anna@beispiel.de"})).email == "anna@beispiel.de"


def test_insecure_picture_url_dropped() -> None:
    assert _validate(good_claims(nonce="n1", picture="http://x/y.png")).picture is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("/rechnungen?x=1", "/rechnungen?x=1"), (None, "/"), ("//evil.com", "/"), ("https://evil.com", "/"),
     ("/\\evil.com", "/"), ("/ok\nX", "/"), ("rechnungen", "/")],
)
def test_safe_next_path(value: str | None, expected: str) -> None:
    assert safe_next_path(value) == expected


def test_pkce_challenge_is_s256_of_verifier() -> None:
    import base64
    import hashlib

    p = new_pkce()
    assert 43 <= len(p.verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(p.verifier.encode()).digest()).rstrip(b"=").decode()
    assert p.challenge == expected


def test_signed_cookie_roundtrip_tamper_and_expiry() -> None:
    token = signed_cookie.sign({"state": "abc"}, "k" * 32, max_age_seconds=60, now=1000)
    assert signed_cookie.verify(token, "k" * 32, now=1030) == {"state": "abc"}
    assert signed_cookie.verify(token, "k" * 32, now=1061) is None            # abgelaufen
    assert signed_cookie.verify(token, "x" * 32, now=1030) is None            # falscher Schlüssel
    body, mac = token.split(".")
    assert signed_cookie.verify(body + "A." + mac, "k" * 32, now=1030) is None  # manipuliert
    assert signed_cookie.verify("kaputt", "k" * 32) is None
    assert signed_cookie.verify(None, "k" * 32) is None


def test_origin_check_logic() -> None:
    mw = OriginCheckMiddleware(app=None, allowed_origin="https://app.beispiel.de")  # type: ignore[arg-type]
    assert mw._allowed("https://app.beispiel.de", None)
    assert not mw._allowed("https://app.beispiel.de.evil.com", None)
    assert mw._allowed(None, "https://app.beispiel.de/rechnungen")
    assert not mw._allowed(None, "https://evil.example/x")
    assert not mw._allowed(None, None)


# ───────────────────────── kompletter Ablauf ─────────────────────────


def _start_login(client: TestClient, google: FakeGoogle, next_path: str = "/rechnungen") -> str:
    r = client.get("/api/auth/google/login", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 302
    location = urlsplit(r.headers["location"])
    assert location.netloc == "accounts.google.com"
    params = parse_qs(location.query)
    assert params["hd"] == ["beispiel.de"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["http://testserver/api/auth/google/callback"]
    assert params["scope"] == ["openid email profile"]
    google.nonce = params["nonce"][0]
    return params["state"][0]


def test_full_login_and_logout(client: TestClient, google: FakeGoogle, store: MemoryAuthStore) -> None:
    state = _start_login(client, google)
    r = client.get("/api/auth/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/rechnungen"
    cookie_header = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookie_header if c.startswith("selbstlite_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    assert google.exchanged and google.exchanged[0][0] == "c1"

    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "anna@beispiel.de"

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert store.events == ["login:anna@beispiel.de", "logout:anna@beispiel.de"]


def test_state_mismatch_is_rejected(client: TestClient, google: FakeGoogle) -> None:
    _start_login(client, google)
    r = client.get("/api/auth/google/callback", params={"code": "c1", "state": "falsch"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=expired"
    assert client.get("/api/auth/me").status_code == 401


def test_callback_without_login_start_is_rejected(client: TestClient) -> None:
    r = client.get("/api/auth/google/callback", params={"code": "c1", "state": "x"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=expired"


def test_foreign_domain_is_rejected(client: TestClient, google: FakeGoogle) -> None:
    state = _start_login(client, google)
    google.claims_overrides = {"hd": None, "email": "privat@gmail.com"}
    r = client.get("/api/auth/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=wrong_domain"
    assert client.get("/api/auth/me").status_code == 401


def test_disabled_user_is_rejected(client: TestClient, google: FakeGoogle, store: MemoryAuthStore) -> None:
    state = _start_login(client, google)
    client.get("/api/auth/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    client.post("/api/auth/logout")
    user, _ = store.users["google-sub-1"]
    store.users["google-sub-1"] = (user, False)
    state = _start_login(client, google)
    r = client.get("/api/auth/google/callback", params={"code": "c2", "state": state}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=disabled"


def test_open_redirect_is_prevented(client: TestClient, google: FakeGoogle) -> None:
    state = _start_login(client, google, next_path="//evil.example/phish")
    r = client.get("/api/auth/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_google_error_param(client: TestClient, google: FakeGoogle) -> None:
    _start_login(client, google)
    r = client.get("/api/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=cancelled"


def test_auth_config_is_public(client: TestClient) -> None:
    cfg = client.get("/api/auth/config").json()
    assert {k: cfg[k] for k in ("google_configured", "domain", "dev_login")} == {"google_configured": True, "domain": "beispiel.de",
                                                                                "dev_login": False}
    assert cfg["microsoft_configured"] is False and cfg["password_enabled"] is False


def test_dev_login_disabled_by_default(client: TestClient) -> None:
    assert client.post("/api/auth/dev-login", json={"email": "anna@beispiel.de"}).status_code == 404


def test_dev_login_when_enabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config

    settings = config.get_settings().model_copy(update={"dev_login_enabled": True})
    monkeypatch.setattr("app.api.auth_routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.auth.deps.get_settings", lambda: settings)
    assert client.post("/api/auth/dev-login", json={"email": "x@fremd.de"}).status_code == 403
    r = client.post("/api/auth/dev-login", json={"email": "anna@beispiel.de", "name": "Anna"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["email"] == "anna@beispiel.de"


def test_dev_login_forbidden_in_production() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="DEV_LOGIN_ENABLED"):
        Settings(app_env="production", app_origin="https://app.beispiel.de", database_url="x",  # type: ignore[arg-type]
                 ledger_internal_token="t" * 40, tenant_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
                 session_secret="s" * 40, dev_login_enabled=True)  # type: ignore[arg-type]
