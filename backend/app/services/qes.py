"""Qualifizierte elektronische Signatur (QES, eIDAS) über einen externen Vertrauensdiensteanbieter – optional einsteckbar.

Die QES ersetzt die gesetzliche Schriftform (§ 126a BGB), z. B. für befristete Arbeitsverträge (§ 14 Abs. 4 TzBfG) oder
Mietverträge über ein Jahr (§ 550 BGB). Sie hilft NICHT, wo die elektronische Form ausgeschlossen ist (§ 623 BGB Kündigung/
Aufhebungsvertrag, § 766 BGB Bürgschaft, §§ 780/781 BGB) – dort bleibt nur Papier.

Anbieter werden über ``QesProvider`` angebunden; mitgeliefert ist ein Adapter für Skribble (API v2, EU-Recht, Qualität „QES“).
Die unterschreibende Person identifiziert sich beim Anbieter (einmalig, z. B. Online-Ausweis/Video-Ident) und bestätigt jede
Unterschrift mit einem zweiten Faktor. Weitere Anbieter (D-Trust sign-me, Swisscom, Namirial, …) lassen sich als Klasse ergänzen.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


class QesError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QesRequest:
    request_id: str
    document_id: str | None
    signing_url: str | None


class QesProvider(Protocol):
    name: str

    async def create(self, *, title: str, message: str, pdf: bytes, signers: list[str]) -> QesRequest: ...
    async def status(self, request_id: str) -> tuple[str, str | None]: ...   # (signed|pending|rejected, document_id)
    async def download(self, document_id: str) -> bytes: ...


class SkribbleProvider:
    """Skribble API v2 (https://api.skribble.com/v2) – Anmeldung mit API-Benutzer und API-Schlüssel (Demo-Schlüssel zum Testen)."""

    name = "skribble"

    def __init__(self, base_url: str, user: str, key: str) -> None:
        self.base = (base_url or "https://api.skribble.com/v2").rstrip("/")
        self.user, self.key = user, key

    async def _token(self, c: httpx.AsyncClient) -> str:
        r = await c.post(f"{self.base}/access/login", json={"username": self.user, "api-key": self.key})
        if r.status_code >= 300:
            raise QesError(f"Skribble-Anmeldung fehlgeschlagen ({r.status_code})")
        return r.text.strip().strip('"')

    async def create(self, *, title: str, message: str, pdf: bytes, signers: list[str]) -> QesRequest:
        async with httpx.AsyncClient(timeout=60) as c:
            token = await self._token(c)
            r = await c.post(f"{self.base}/signature-requests", headers={"Authorization": f"Bearer {token}"}, json={
                "title": title[:255], "message": message[:1000], "content": base64.b64encode(pdf).decode(),
                "quality": "QES", "legislation": "EU", "signatures": [{"account_email": e} for e in signers]})
        if r.status_code >= 300:
            raise QesError(f"Skribble: Signaturanfrage abgelehnt ({r.status_code}): {r.text[:200]}")
        d = r.json()
        return QesRequest(str(d["id"]), str(d.get("document_id") or "") or None, d.get("signing_url"))

    async def status(self, request_id: str) -> tuple[str, str | None]:
        async with httpx.AsyncClient(timeout=30) as c:
            token = await self._token(c)
            r = await c.get(f"{self.base}/signature-requests/{request_id}", headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 300:
            raise QesError(f"Skribble: Status nicht abrufbar ({r.status_code})")
        d = r.json()
        overall = str(d.get("status_overall") or "").upper()
        state = "signed" if overall == "SIGNED" else "rejected" if overall in ("DECLINED", "WITHDRAWN") else "pending"
        return state, str(d.get("document_id") or "") or None

    async def download(self, document_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as c:
            token = await self._token(c)
            r = await c.get(f"{self.base}/documents/{document_id}/content", headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 300 or not r.content.startswith(b"%PDF"):
            raise QesError("Skribble: signiertes Dokument nicht abrufbar")
        return r.content


PROVIDERS: dict[str, Any] = {"skribble": SkribbleProvider}
LABELS = {"none": "kein Anbieter", "skribble": "Skribble (QES nach eIDAS)"}


def provider() -> QesProvider | None:
    s = get_settings()
    cls = PROVIDERS.get(s.qes_provider)
    if cls is None:
        return None
    if not s.qes_api_user or not s.qes_api_key.get_secret_value():
        return None
    p: QesProvider = cls(s.qes_base_url, s.qes_api_user, s.qes_api_key.get_secret_value())
    return p


def info() -> dict[str, Any]:
    s = get_settings()
    p = provider()
    return {"configured": p is not None, "provider": s.qes_provider, "label": LABELS.get(s.qes_provider, s.qes_provider)}
