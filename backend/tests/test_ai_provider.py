"""Eingebaute lokale KI (Ollama): Bereitschaft während des automatischen Modell-Downloads."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.ai import AIError, OllamaProvider


def provider(models: list[str], chat: dict[str, Any] | None = None, down: bool = False) -> tuple[OllamaProvider, list[str]]:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if down:
            raise httpx.ConnectError("refused")
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        return httpx.Response(200, json={"message": {"content": json.dumps(chat or {})}})

    p = OllamaProvider("http://ollama:11434", "qwen2.5vl:3b")
    p._client = httpx.AsyncClient(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))
    return p, calls


async def test_ready_when_model_present() -> None:
    p, _ = provider(["qwen2.5vl:3b", "other:1b"])
    assert await p.status() == (True, None)


async def test_not_ready_while_downloading_and_no_inference_call() -> None:
    p, calls = provider([])
    ready, hint = await p.status()
    assert not ready and hint and "heruntergeladen" in hint
    with pytest.raises(AIError, match="heruntergeladen"):
        await p.complete_json(system="s", text="t", file=None, schema={})
    assert "/api/chat" not in calls, "solange das Modell fehlt, wird keine Anfrage gestellt"


async def test_unreachable_service() -> None:
    p, _ = provider([], down=True)
    ready, hint = await p.status()
    assert not ready and hint and "nicht erreichbar" in hint


async def test_completion_when_ready() -> None:
    p, calls = provider(["qwen2.5vl:3b"], chat={"account": "4930"})
    assert await p.complete_json(system="s", text="t", file=None, schema={"type": "object"}) == {"account": "4930"}
    assert calls == ["/api/tags", "/api/chat"]


async def test_digital_pdf_is_sent_as_text_without_images() -> None:
    from app.demo import pdf

    sent: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5vl:3b"}]})
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "{}"}})

    p = OllamaProvider("http://ollama:11434", "qwen2.5vl:3b")
    p._client = httpx.AsyncClient(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))
    doc = pdf("Steuerkanzlei Weber - Rechnung SW202611 - Finanzbuchhaltung 11/2026 - netto 450,00 EUR zzgl. 19 % USt")
    await p.complete_json(system="s", text="Beleg", file=(doc, "application/pdf"), schema={})
    user = sent[0]["messages"][1]
    assert "Finanzbuchhaltung" in user["content"] and "images" not in user


def test_large_images_are_shrunk() -> None:
    import io

    from PIL import Image

    from app.services.ai import MAX_IMAGE_SIDE, shrink_image

    buf = io.BytesIO()
    Image.new("RGB", (2480, 3508), "white").save(buf, format="JPEG")
    with Image.open(io.BytesIO(shrink_image(buf.getvalue()))) as out:
        assert max(out.size) == MAX_IMAGE_SIDE


def test_single_line_assignment_uses_full_amount_and_split_must_match() -> None:
    from decimal import Decimal

    from app.services.accounting import AccountingError, assign_schema, validate_assignment

    lines = validate_assignment({"lines": [{"account": "6830", "amount": "999.99", "note": "x"}]},
                                base=Decimal("450.00"), allowed={"6830", "6825"})
    assert [(ln.account, ln.debit) for ln in lines] == [("6830", Decimal("450.00"))]
    with pytest.raises(AccountingError, match="nicht exakt"):
        validate_assignment({"lines": [{"account": "6830", "amount": "300.00"}, {"account": "6825", "amount": "100.00"}]},
                            base=Decimal("450.00"), allowed={"6830", "6825"})
    with pytest.raises(AccountingError, match="nicht zulässig"):
        validate_assignment({"lines": [{"account": "1000", "amount": "450.00"}]}, base=Decimal("450.00"), allowed={"6830"})
    schema = assign_schema(["6830", "6825"])
    item = schema["properties"]["lines"]
    assert item["maxItems"] == 3 and item["items"]["properties"]["account"]["enum"] == ["6830", "6825"]


def test_ai_may_only_choose_expense_and_fixed_asset_accounts() -> None:
    from app.services.accounting import ai_accounts

    accounts = [{"number": n, "type": t} for n, t in [("0650", "asset"), ("0670", "asset"), ("1200", "asset"), ("1406", "asset"),
                                                       ("1800", "asset"), ("1600", "asset"), ("6830", "expense"), ("3300", "liability")]]
    assert [a["number"] for a in ai_accounts(accounts)] == ["0650", "0670", "6830"], "keine Bank-, Kassen-, Forderungs- oder Vorsteuerkonten"
