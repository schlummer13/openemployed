import asyncio
import datetime as dt
import os
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest

# Testkonfiguration – vor dem Import der App setzen
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_ORIGIN", "http://testserver")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test")
os.environ.setdefault("LEDGER_URL", "http://127.0.0.1:1")
os.environ.setdefault("LEDGER_INTERNAL_TOKEN", "t" * 64)
os.environ.setdefault("DOCUMENSO_WEBHOOK_SECRET", "w" * 40)
os.environ.setdefault("TENANT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("SESSION_SECRET", "s" * 64)
os.environ.setdefault("GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "geheim")
os.environ.setdefault("AUTH_ALLOWED_DOMAIN", "beispiel.de")

from fastapi.testclient import TestClient

from app.auth.store import MemoryAuthStore, User
from app.main import app
from app.services.ledger_client import LedgerError, SealedRecord, to_ledger_payload

ORIGIN = "http://testserver"
CLIENT_ID = "client-123.apps.googleusercontent.com"


def good_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "anna@beispiel.de",
        "email_verified": True,
        "hd": "beispiel.de",
        "name": "Anna Gründerin",
        "picture": "https://lh3.googleusercontent.com/a/x",
        "nonce": "PLACEHOLDER",
    }
    claims.update(overrides)
    return claims


class FakeGoogle:
    """Ersetzt Google: liefert konfigurierbare Claims und übernimmt die nonce aus dem Anmeldestart."""

    def __init__(self) -> None:
        self.claims_overrides: dict[str, Any] = {}
        self.nonce = ""
        self.exchanged: list[tuple[str, str]] = []

    async def exchange_code(self, code: str, code_verifier: str) -> str:
        self.exchanged.append((code, code_verifier))
        return "id-token"

    async def verify_id_token(self, id_token: str) -> dict[str, Any]:
        return good_claims(nonce=self.nonce, **self.claims_overrides)


class FakeMailer:
    mailbox = "rechnungen@beispiel.de"

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, mail: Any) -> str:
        self.sent.append(mail)
        return "<test@beispiel.de>"


def reset_app_state() -> None:
    """Zustand der App zwischen Tests zurücksetzen (die App ist ein Modul-Singleton)."""
    for attr in ("engine", "ledger", "auth_store", "google", "mailer", "documenso", "extractor", "ai"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


class FakeLedger:
    """Verhält sich wie der Rust-Ledger: lückenlose Nummern je Jahr, Abfrage je Vorgang, optionaler Ausfall."""

    def __init__(self) -> None:
        self.records: list[SealedRecord] = []
        self.counters: dict[tuple[str, int], int] = {}
        self.fail_after_seal = False
        self.locked: set[str] = set()

    async def health(self) -> bool:
        return True

    async def seal(self, *, tenant_id: UUID, record_type: str, payload: Any, sealed_by: str,
                   subject_id: UUID | None = None, number_range: tuple[str, str] | None = None,
                   issue_date: dt.date | None = None, posting_period: str | None = None) -> SealedRecord:
        import re as _re

        if posting_period is not None and posting_period in self.locked:
            raise LedgerError(409, "conflict", f"Periode {posting_period} ist festgeschrieben")
        number = None
        if number_range:
            assert issue_date is not None
            key = (number_range[0], issue_date.year)
            self.counters[key] = self.counters.get(key, 0) + 1
            seq = self.counters[key]
            number = _re.sub(r"\{SEQ:(\d+)\}", lambda m: f"{seq:0{int(m.group(1))}d}",
                             number_range[1].replace("{YYYY}", str(issue_date.year)))
        record = SealedRecord(
            id=uuid4(), seq=len(self.records) + 1, tenant_id=tenant_id, record_type=record_type,
            subject_id=subject_id, number_range_key=number_range[0] if number_range else None,
            document_number=number, payload=to_ledger_payload(payload), prev_hash="0" * 64,
            hash=f"{len(self.records) + 1:064x}", sealed_at="2026-11-14T12:00:00.000000Z", sealed_by=sealed_by,
        )
        self.records.append(record)
        if self.fail_after_seal:
            raise LedgerError(502, "network", "Antwort verloren")
        return record

    async def verify(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(ok=not getattr(self, "broken", False), records_checked=len(self.records))

    async def locked_periods(self, *, tenant_id: UUID) -> list[str]:
        return sorted(self.locked)

    async def lock_period(self, *, tenant_id: UUID, period: str, sealed_by: str) -> SealedRecord:
        if period in self.locked:
            raise LedgerError(409, "conflict", f"Periode {period} ist bereits gesperrt")
        self.locked.add(period)
        return await self.seal(tenant_id=tenant_id, record_type="period.lock", payload={"period": period}, sealed_by=sealed_by)

    async def records_by_subject(self, *, tenant_id: UUID, subject_id: UUID) -> list[SealedRecord]:
        return [r for r in self.records if r.subject_id == subject_id and r.tenant_id == tenant_id]

    async def aclose(self) -> None:
        return None


@pytest.fixture
def store() -> MemoryAuthStore:
    return MemoryAuthStore()


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def client(store: MemoryAuthStore, google: FakeGoogle, mailer: FakeMailer) -> Iterator[TestClient]:
    reset_app_state()
    app.state.auth_store = store
    app.state.google = google
    app.state.mailer = mailer
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        yield c


@pytest.fixture
def auth_client(client: TestClient, store: MemoryAuthStore) -> TestClient:
    user = User(id=uuid4(), email="anna@beispiel.de", name="Anna", picture=None)
    token = asyncio.run(store.create_session(user, ip=None, user_agent=None))
    client.cookies.set("openemployed_session", token)
    return client


@pytest.fixture(autouse=True)
def pdf_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Pango (lokal) wird das PDF durch eine Attrappe ersetzt; im Test-Container läuft das echte Rendering."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        from app.einvoice import pdf as pdf_module

        monkeypatch.setattr(pdf_module, "render_facturx_pdf",
                            lambda number, snapshot, xml, logo: b"%PDF-1.7 fake " + number.encode() + b"\n" + xml)
        monkeypatch.setattr(pdf_module, "render_offer_pdf",
                            lambda number, snapshot, logo: b"%PDF-1.7 fake offer " + number.encode())
        monkeypatch.setattr(pdf_module, "render_plain_pdf", lambda html: b"%PDF-1.7 fake plain " + html[:40].encode())
        from app.services import documenso as ds_module

        monkeypatch.setattr(ds_module, "with_signature_page", lambda pdf, html: (pdf + b"\n%signature-page", 2))
        monkeypatch.setattr(pdf_module, "render_dunning_pdf",
                            lambda number, snapshot, logo: b"%PDF-1.7 fake dunning " + number.encode())
