import datetime as dt
import json
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from app.services.ledger_client import LedgerClient, LedgerError, to_ledger_payload

TENANT = UUID("00000000-0000-0000-0000-000000000001")


def test_payload_conversion() -> None:
    payload = {"total": Decimal("2092.02"), "date": dt.date(2026, 11, 14), "id": TENANT, "lines": [Decimal("1.5")]}
    assert to_ledger_payload(payload) == {
        "total": "2092.02",
        "date": "2026-11-14",
        "id": str(TENANT),
        "lines": ["1.5"],
    }


def test_payload_rejects_float() -> None:
    with pytest.raises(TypeError):
        to_ledger_payload({"total": 1.5})


async def test_seal_sends_token_and_parses_response() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers["x-internal-token"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={
            "id": "11111111-1111-1111-1111-111111111111", "seq": 1, "tenant_id": str(TENANT),
            "record_type": "invoice.issued", "subject_id": None, "number_range_key": "invoice",
            "document_number": "RE-2026-00001", "payload": {"total": "2092.02"}, "prev_hash": "0" * 64,
            "hash": "a" * 64, "sealed_at": "2026-11-14T12:00:00.000000Z", "sealed_by": "user:1",
        })

    client = LedgerClient("http://ledger", "s" * 40, transport=httpx.MockTransport(handler))
    record = await client.seal(
        tenant_id=TENANT, record_type="invoice.issued", payload={"total": Decimal("2092.02")},
        sealed_by="user:1", number_range=("invoice", "RE-{YYYY}-{SEQ:5}"), issue_date=dt.date(2026, 11, 14),
    )
    await client.aclose()
    assert record.document_number == "RE-2026-00001"
    assert seen["token"] == "s" * 40
    assert seen["body"] == {
        "tenant_id": str(TENANT), "record_type": "invoice.issued", "sealed_by": "user:1",
        "payload": {"total": "2092.02"}, "number_range": {"key": "invoice", "pattern": "RE-{YYYY}-{SEQ:5}"},
        "issue_date": "2026-11-14",
    }


async def test_errors_are_mapped() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {"code": "conflict", "message": "Periode 2026-10 ist bereits gesperrt"}})

    client = LedgerClient("http://ledger", "s" * 40, transport=httpx.MockTransport(handler))
    with pytest.raises(LedgerError) as exc:
        await client.lock_period(tenant_id=TENANT, period="2026-10", sealed_by="user:1")
    await client.aclose()
    assert exc.value.status_code == 409
    assert exc.value.code == "conflict"
