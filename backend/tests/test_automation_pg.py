"""Automatisierungen: Fälligkeit, Läufe, Belegpostfach, Aufgabenliste."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.main import app
from app.services.automation import is_due
from app.services.invoices import BERLIN
from app.services.mail import Attachment, IncomingMail
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401


def t(h: int, m: int = 0, d: int = 16) -> dt.datetime:
    return dt.datetime(2026, 11, d, h, m, tzinfo=BERLIN)  # 16.11.2026 = Montag


def test_is_due() -> None:
    assert not is_due("daily@6", t(5, 59), None)
    assert is_due("daily@6", t(6), None)
    assert not is_due("daily@6", t(9), t(6, 1))
    assert is_due("daily@6", t(6), t(6, 1, d=15))
    assert is_due("every@15", t(10), t(9, 45)) and not is_due("every@15", t(10), t(9, 50))
    assert is_due("weekly@0@8", t(8, 5), None) and not is_due("weekly@0@8", t(8, 5, d=17), None)
    assert not is_due("weekly@0@8", t(9), t(8, 1))


class InboxMailer:
    def __init__(self, mails: list[IncomingMail]) -> None:
        self.mails = mails
        self.processed: list[str] = []
        self.sent: list[Any] = []

    async def fetch_unprocessed(self, limit: int = 50) -> list[IncomingMail]:
        return [m for m in self.mails if m.uid not in self.processed][:limit]

    async def mark_processed(self, uid: str) -> None:
        self.processed.append(uid)

    async def send(self, mail: Any) -> str:
        self.sent.append(mail)
        return "<x@y>"


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_receipt_inbox_and_tasks(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    xml = (await api.get(f"/api/invoices/{inv['id']}/xml")).content

    overview = (await api.get("/api/automations")).json()
    keys = {a["key"]: a for a in overview}
    assert keys["receipt_inbox"]["enabled"] and "bank_sync" not in keys, "Qonto-Job entfernt"

    mailer = InboxMailer([
        IncomingMail("1", "<m1@lieferant>", "rechnung@lieferant.de", "Ihre Rechnung", None, "", (
            Attachment("rechnung.xml", "application/xml", xml), Attachment("logo.gif", "image/gif", b"GIF89a"))),
        IncomingMail("2", "<m2@lieferant>", "foto@x.de", "Quittung", None, "", (
            Attachment("quittung.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\x02" * 40),)),
    ])
    app.state.mailer = mailer
    app.state.extractor = None
    r = (await api.post("/api/automations/receipt_inbox/run")).json()
    assert r["status"] == "ok" and r["summary"].startswith("2 Mails, 2 Dateien, 1 Belegentwürfe"), r
    assert mailer.processed == ["1", "2"]
    receipts = (await api.get("/api/receipts")).json()
    assert len(receipts) == 1 and receipts[0]["status"] == "draft" and receipts[0]["invoice_number"] == inv["number"]
    assert receipts[0]["supplier_name"].startswith("Beispiel Software"), "Lieferant automatisch angelegt"
    unassigned = (await api.get("/api/receipt-inbox/unassigned")).json()
    assert [u["file_name"] for u in unassigned] == ["quittung.png"]

    # Gleiche Mail erneut (z. B. Label nicht gesetzt) → keine Dublette
    mailer.processed.clear()
    await api.post("/api/automations/receipt_inbox/run")
    assert len((await api.get("/api/receipts")).json()) == 1

    dash = (await api.get("/api/dashboard")).json()
    titles = " | ".join(x["title"] for x in dash["tasks"])
    assert "Belegentwurf" in titles and "Belegdatei" in titles and Decimal_like(dash["kpis"]["open_receivables"])

    # Abschalten wirkt, manuelle Läufe bleiben möglich; Protokoll vorhanden
    assert (await api.put("/api/automations/receipt_inbox", json={"enabled": False})).status_code == 200
    assert {a["key"]: a for a in (await api.get("/api/automations")).json()}["receipt_inbox"]["enabled"] is False
    last = {a["key"]: a for a in (await api.get("/api/automations")).json()}["receipt_inbox"]["last"]
    assert last["status"] == "ok" and last["trigger"] == "manual"
    assert (await api.post("/api/automations/unknown/run")).status_code == 404

    cleanup = (await api.post("/api/automations/cleanup/run")).json()
    assert cleanup["status"] == "ok"
    digest = (await api.post("/api/automations/digest/run")).json()
    assert digest["status"] in ("ok", "skipped")


def Decimal_like(v: str) -> bool:
    return isinstance(v, str) and "." in v
