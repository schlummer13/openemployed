"""Kontoauszugsformate und FinTS-Ablauf (mit Attrappe)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import fints_bank
from app.services.bank import BankError
from app.services.bank_formats import parse_any

CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>
<Acct><Id><IBAN>DE89370400440532013000</IBAN></Id></Acct>
<Ntry><Amt Ccy="EUR">2092.02</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-11-02</Dt></BookgDt>
<AcctSvcrRef>BANKREF1</AcctSvcrRef><NtryDtls><TxDtls><RltdPties><Dbtr><Nm>HR 1 GmbH</Nm></Dbtr><DbtrAcct><Id><IBAN>DE02120300000000202051</IBAN></Id></DbtrAcct></RltdPties>
<RmtInf><Ustrd>RE-2026-00001</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="EUR">49.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-11-03</Dt></BookgDt>
<NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Hetzner Online GmbH</Nm></Cdtr></RltdPties><RmtInf><Ustrd>Server</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="EUR">10.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>PDNG</Cd></Sts><BookgDt><Dt>2026-11-04</Dt></BookgDt></Ntry>
</Stmt></BkToCstmrStmt></Document>"""

MT940 = b""":20:STARTUMS
:25:37040044/0532013000
:28C:00001/001
:60F:C261101EUR1000,00
:61:2611021102CR2092,02NMSCNONREF
:86:166?00GUTSCHRIFT?20RE-2026-00001?32HR 1 GmbH
:62F:C261102EUR3092,02
-
"""


def test_camt() -> None:
    rows = parse_any(CAMT, "auszug.xml")
    assert len(rows) == 2, "vorgemerkte Umsätze werden ignoriert"
    credit, debit = rows
    assert credit["amount"] == Decimal("2092.02") and credit["counterparty"] == "HR 1 GmbH" and credit["external_id"] == "BANKREF1"
    assert credit["reference"] == "RE-2026-00001" and credit["account_iban"] == "DE89370400440532013000"
    assert debit["amount"] == Decimal("-49.00") and debit["counterparty"] == "Hetzner Online GmbH"
    assert parse_any(CAMT, "auszug.xml")[1]["external_id"] == debit["external_id"], "stabile ID ohne Bankreferenz"


def test_mt940() -> None:
    rows = parse_any(MT940, "auszug.sta")
    assert len(rows) == 1 and rows[0]["amount"] == Decimal("2092.02") and rows[0]["booked_on"] == dt.date(2026, 11, 2)
    assert "RE-2026-00001" in (rows[0]["reference"] or "")


def test_xxe_rejected() -> None:
    with pytest.raises(BankError):
        parse_any(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>', "a.xml")


class FakeClient:
    """Bildet den python-fints-Dialog nach: erst TAN, dann Umsätze."""

    def __init__(self, params: dict[str, Any], from_data: bytes | None = None) -> None:
        self.params, self.from_data = params, from_data
        self.init_tan_response = None

    def get_current_tan_mechanism(self) -> str:
        return "921"

    def is_tan_media_required(self) -> bool:
        return False

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def get_sepa_accounts(self) -> list[Any]:
        return [SimpleNamespace(iban="DE89370400440532013000")]

    def get_transactions(self, account: Any, start: dt.date, end: dt.date) -> Any:
        from fints.client import NeedTANResponse

        resp = NeedTANResponse.__new__(NeedTANResponse)
        resp.challenge = "Bitte in der App freigeben"
        resp.decoupled = True
        resp.challenge_html = None
        resp.get_data = lambda: b"tan-data"  # type: ignore[method-assign]
        return resp

    def deconstruct(self, including_private: bool = False) -> bytes:
        return b"client"

    def pause_dialog(self) -> bytes:
        return b"dialog"

    def resume_dialog(self, data: bytes) -> FakeClient:
        assert data == b"dialog"
        return self

    def send_tan(self, challenge: Any, tan: str) -> list[Any]:
        amount = SimpleNamespace(amount=Decimal("119.00"))
        return [SimpleNamespace(data={"amount": amount, "date": dt.date(2026, 11, 5), "applicant_name": "Max Muster",
                                      "purpose": "RE-2026-00003", "bank_reference": "B1"})]


def test_fints_tan_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from fints.client import NeedTANResponse

    monkeypatch.setattr(NeedTANResponse, "from_data", staticmethod(lambda data: "challenge"))
    params = {"bank_code": "37040044", "server_url": "https://fints.example", "login": "u", "pin": "1234",
              "iban": "DE89 3704 0044 0532 0130 00", "product_id": "TEST", "start": dt.date(2026, 10, 1), "end": dt.date(2026, 11, 5)}
    with pytest.raises(BankError):
        fints_bank.start(params | {"product_id": ""}, factory=FakeClient)
    first = fints_bank.start(params, factory=FakeClient)
    assert first["status"] == "tan" and first["decoupled"] is True and "pin" not in str(first)
    done = fints_bank.proceed(first["token"], "", factory=FakeClient)
    assert done["status"] == "ok" and done["rows"][0]["amount"] == Decimal("119.00") and done["rows"][0]["external_id"] == "B1"
    with pytest.raises(BankError):
        fints_bank.proceed(first["token"], "", factory=FakeClient)  # Token nur einmal gültig
