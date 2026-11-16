"""Schriftform-Wege: qualifizierte Signatur über externen Anbieter, Ausdruck mit Scan-Rücklauf, E-Mail; Personalakte."""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.einvoice.pdf import render_plain_pdf
from app.main import app
from app.services import qes
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
PDF = render_plain_pdf("<h1>Mietvertrag</h1>")
SIGNED = render_plain_pdf("<h1>qualifiziert signiert</h1>")


class FakeQes:
    name = "fake"
    requests: ClassVar[list[dict[str, Any]]] = []
    state = "pending"

    def __init__(self, *_: Any) -> None:
        pass

    async def create(self, *, title: str, message: str, pdf: bytes, signers: list[str]) -> qes.QesRequest:
        FakeQes.requests.append({"title": title, "signers": signers})
        return qes.QesRequest("req-1", "doc-1", "https://qes.example/sign/req-1")

    async def status(self, request_id: str) -> tuple[str, str | None]:
        return FakeQes.state, "doc-1"

    async def download(self, document_id: str) -> bytes:
        return SIGNED


@pytest.fixture
def fake_qes(monkeypatch: pytest.MonkeyPatch) -> type[FakeQes]:
    s = get_settings()
    monkeypatch.setitem(qes.PROVIDERS, "fake", FakeQes)
    for k, v in (("qes_provider", "fake"), ("qes_api_user", "u"), ("qes_api_key", SecretStr("k"))):
        object.__setattr__(s, k, v)
    FakeQes.requests, FakeQes.state = [], "pending"
    yield FakeQes
    for k, v in (("qes_provider", "none"), ("qes_api_user", ""), ("qes_api_key", SecretStr(""))):
        object.__setattr__(s, k, v)


def form(**kw: str) -> dict[str, str]:
    return {"title": "Mietvertrag Lager", "kind": "lease", "counterparty": "Vera Vermietung", "signer_name": "Vera",
            "signer_email": "vera@vermietung.de"} | kw


async def test_qes_paper_and_mail(env: Env, fake_qes: type[FakeQes]) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    lst = (await api.get("/api/signatures/documents")).json()
    assert lst["qes"]["configured"] is True
    # Qualifiziert über den Anbieter → offen → Abgleich holt das signierte PDF
    doc = (await api.post("/api/signatures/documents", data=form(), files={"file": ("m.pdf", PDF, "application/pdf")})).json()
    r = await api.post(f"/api/signatures/documents/{doc['id']}/qes")
    assert r.status_code == 201 and r.json()["signing_url"].startswith("https://")
    assert fake_qes.requests[0]["signers"][0] == "vera@vermietung.de"
    assert (await api.post("/api/signatures/qes-sync")).json()["open"] == 1
    fake_qes.state = "signed"
    assert (await api.post("/api/signatures/qes-sync")).json()["signed"] == 1
    d = next(x for x in (await api.get("/api/signatures/documents")).json()["documents"] if x["id"] == doc["id"])
    assert d["status"] == "signed" and d["channel"] == "qes" and d["archive_document_id"]

    # Kündigung: auch QES genügt nicht → Ausdrucken, Scan hochladen
    kd = (await api.post("/api/signatures/documents", data=form(title="Kündigung Mietvertrag Lager", kind="general"),
                         files={"file": ("k.pdf", PDF, "application/pdf")})).json()
    assert kd["form_level"] == "block"
    r = await api.post(f"/api/signatures/documents/{kd['id']}/qes")
    assert r.status_code == 409 and "ausdrucken" in r.text
    assert (await api.post(f"/api/signatures/documents/{kd['id']}/paper", json={"note": "persönlich übergeben"})).json()["status"] == "awaiting_paper"
    up = await api.post(f"/api/signatures/documents/{kd['id']}/signed-copy", files={"file": ("scan.pdf", SIGNED, "application/pdf")})
    assert up.status_code == 200 and up.json()["status"] == "signed"

    # E-Mail mit PDF-Anhang und Bitte um Original
    em = (await api.post("/api/signatures/documents", data=form(title="Mietvertrag Garage"), files={"file": ("g.pdf", PDF, "application/pdf")})).json()
    sent_before = len(app.state.mailer.sent)
    r = await api.post(f"/api/signatures/documents/{em['id']}/mail", json={"note": "Bitte bis Monatsende"})
    assert r.status_code == 200 and r.json()["status"] == "awaiting_paper"
    mail = app.state.mailer.sent[sent_before]
    assert mail.to == ["vera@vermietung.de"] and mail.attachments and "Original" in mail.text and "Bitte bis Monatsende" in mail.text


async def test_personnel_handoff(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    today = dt.date.today()  # noqa: DTZ011
    emp = (await api.post("/api/payroll/employees", json={
        "first_name": "Kim", "last_name": "Kündigung", "email": "kim@firma.de", "entry_date": "2024-01-01", "salary_type": "monthly",
        "gross_month": "3000", "weekly_hours": "40", "iban": "DE02120300000000202051"})).json()
    up = await api.post(f"/api/personnel/{emp['id']}/documents", data={"category": "termination", "title": f"Aufhebungsvertrag {today:%Y}"},
                        files={"file": ("a.pdf", PDF, "application/pdf")})
    did = up.json()["id"]
    assert (await api.post(f"/api/personnel/documents/{did}/handoff", json={"channel": "qes"})).status_code == 409, "kein Anbieter / ausgeschlossen"
    r = await api.post(f"/api/personnel/documents/{did}/handoff", json={"channel": "email"})
    assert r.status_code == 201 and r.json()["status"] == "awaiting_paper"
    sid = r.json()["sign_document_id"]
    assert (await api.post(f"/api/signatures/documents/{sid}/signed-copy", files={"file": ("s.pdf", SIGNED, "application/pdf")})).status_code == 200
    files = (await api.get(f"/api/personnel/{emp['id']}/documents")).json()["documents"]
    assert any("signiert" in f["title"] and f["signature_status"] == "signed" for f in files)
