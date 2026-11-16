"""Client für den Rust-Festschreibungsdienst (nur internes Netz).

Beträge in Nutzdaten müssen als Strings übergeben werden; ``to_ledger_payload`` wandelt
``Decimal`` sicher um und lehnt Fließkommazahlen ab.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel


class LedgerError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"Ledger {status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


class SealedRecord(BaseModel):
    id: UUID
    seq: int
    tenant_id: UUID
    record_type: str
    subject_id: UUID | None
    number_range_key: str | None
    document_number: str | None
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    sealed_at: str
    sealed_by: str


class VerifyReport(BaseModel):
    ok: bool
    records_checked: int
    head_seq: int | None
    head_hash: str | None
    first_break: dict[str, Any] | None


def to_ledger_payload(value: Any) -> Any:
    """Rekursiv JSON-sicher machen: Decimal → String, UUID/Datum → ISO-String, float verboten."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        raise TypeError("Fließkommazahlen dürfen nicht festgeschrieben werden – Decimal verwenden")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("ungültiger Betrag")
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): to_ledger_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_ledger_payload(v) for v in value]
    raise TypeError(f"Nicht festschreibbarer Typ: {type(value).__name__}")


class LedgerClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"x-internal-token": token},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        code, message = "error", resp.text[:500]
        try:
            err = resp.json()["error"]
            code, message = str(err["code"]), str(err["message"])
        except (ValueError, KeyError, TypeError):
            pass
        raise LedgerError(resp.status_code, code, message)

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/health")
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def seal(
        self,
        *,
        tenant_id: UUID,
        record_type: str,
        payload: Mapping[str, Any],
        sealed_by: str,
        subject_id: UUID | None = None,
        number_range: tuple[str, str] | None = None,
        issue_date: dt.date | None = None,
        posting_period: str | None = None,
    ) -> SealedRecord:
        body: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "record_type": record_type,
            "sealed_by": sealed_by,
            "payload": to_ledger_payload(payload),
        }
        if subject_id is not None:
            body["subject_id"] = str(subject_id)
        if number_range is not None:
            body["number_range"] = {"key": number_range[0], "pattern": number_range[1]}
        if issue_date is not None:
            body["issue_date"] = issue_date.isoformat()
        if posting_period is not None:
            body["posting_period"] = posting_period
        resp = await self._client.post("/v1/seal", json=body)
        self._raise_for(resp)
        return SealedRecord.model_validate(resp.json())

    async def lock_period(self, *, tenant_id: UUID, period: str, sealed_by: str) -> SealedRecord:
        resp = await self._client.post(
            "/v1/periods/lock", json={"tenant_id": str(tenant_id), "period": period, "sealed_by": sealed_by}
        )
        self._raise_for(resp)
        return SealedRecord.model_validate(resp.json())

    async def is_period_locked(self, *, tenant_id: UUID, period: str) -> bool:
        resp = await self._client.get(f"/v1/periods/{tenant_id}/{period}")
        self._raise_for(resp)
        return bool(resp.json()["locked"])

    async def records_by_subject(self, *, tenant_id: UUID, subject_id: UUID) -> list[SealedRecord]:
        resp = await self._client.get(f"/v1/tenants/{tenant_id}/subjects/{subject_id}/records")
        self._raise_for(resp)
        return [SealedRecord.model_validate(r) for r in resp.json()]

    async def locked_periods(self, *, tenant_id: UUID) -> list[str]:
        resp = await self._client.get(f"/v1/tenants/{tenant_id}/locked-periods")
        self._raise_for(resp)
        return [str(p) for p in resp.json()]

    async def verify(self) -> VerifyReport:
        resp = await self._client.get("/v1/verify")
        self._raise_for(resp)
        return VerifyReport.model_validate(resp.json())
