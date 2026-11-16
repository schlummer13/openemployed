"""KI-Anbieter für Belegerkennung und Kontierung – austauschbar.

- ``ollama`` (empfohlen): lokale KI auf eigener Hardware (z. B. Qwen2.5-VL 7B über Ollama). Keine Kosten je Beleg,
  keine Daten verlassen den eigenen Server. Strukturierte Ausgabe per JSON-Schema (Ollama ``format``).
- ``anthropic``: Claude über die Anthropic-API (optional).
- ``none``: ohne KI – E-Rechnungen werden trotzdem deterministisch gelesen, alles andere manuell.

Alle Anbieter liefern nur Vorschläge; Prüfung und Buchung erfolgen im Code nach festen Regeln.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any, Protocol, cast

import httpx

log = logging.getLogger("selbstlite.ai")


class AIError(RuntimeError):
    pass


class AIProvider(Protocol):
    kind: str
    model: str

    @property
    def label(self) -> str: ...

    async def complete_json(self, *, system: str, text: str, file: tuple[bytes, str] | None, schema: dict[str, Any]) -> dict[str, Any]: ...

    async def status(self) -> tuple[bool, str | None]: ...


MAX_IMAGE_SIDE = 1024  # Bildmodelle auf CPU: längste Seite begrenzen (Qwen 2.5 VL ≈ 1 Token je 28×28 px)
MIN_PDF_TEXT = 80  # ab so viel extrahiertem Text gilt ein PDF als digital (kein Scan) → nur Text an das Modell


def shrink_image(data: bytes, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    """Verkleinert ein Bild (PNG/JPEG) auf max_side Pixel an der längsten Seite; kleinere Bilder bleiben unverändert."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as src:
        if max(src.size) <= max_side:
            return data
        img = src.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def pdf_to_text_and_images(content: bytes, max_pages: int = 3) -> tuple[str, list[bytes]]:
    """Text aus PDF (für textbasierte PDFs) und PNG-Seitenbilder (für Scans/lokale Bildmodelle)."""
    text = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages]).strip()
    except Exception:
        log.debug("PDF-Text nicht lesbar", exc_info=True)
    images: list[bytes] = []
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(content)
        for i in range(min(len(pdf), max_pages)):
            bitmap = pdf[i].render(scale=150 / 72)
            img = bitmap.to_pil()
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            images.append(buf.getvalue())
    except Exception:
        log.debug("PDF-Seiten nicht renderbar", exc_info=True)
    return text, images


class OllamaProvider:
    kind = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 300.0) -> None:
        self.model = model
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    @property
    def label(self) -> str:
        return f"Lokale KI ({self.model})"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def status(self) -> tuple[bool, str | None]:
        """(bereit, Hinweis) – das Modell wird beim ersten Start des Ollama-Dienstes automatisch geladen."""
        try:
            resp = await self._client.get("/api/tags", timeout=5)
            resp.raise_for_status()
            names = {m.get("name") for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return False, "Lokale KI startet noch oder ist nicht erreichbar."
        if self.model in names or f"{self.model}:latest" in names:
            return True, None
        return False, f"Das Modell {self.model} wird gerade heruntergeladen (einmalig, einige Minuten)."

    async def complete_json(self, *, system: str, text: str, file: tuple[bytes, str] | None, schema: dict[str, Any]) -> dict[str, Any]:
        ready, hint = await self.status()
        if not ready:
            raise AIError(hint or "Lokale KI nicht bereit")
        images: list[str] = []
        prompt = text
        if file is not None:
            data, mime = file
            if mime == "application/pdf":
                pdf_text, pages = pdf_to_text_and_images(data, max_pages=2)
                if len(pdf_text) >= MIN_PDF_TEXT:
                    # digitales PDF: der Text reicht und ist auf CPU um ein Vielfaches schneller als Seitenbilder
                    prompt = f"{text}\n\nText des Dokuments:\n{pdf_text[:20000]}"
                else:
                    images = [base64.b64encode(p).decode("ascii") for p in pages]
            elif mime in ("image/png", "image/jpeg"):
                images = [base64.b64encode(shrink_image(data)).decode("ascii")]
        # num_predict begrenzt die Antwortlänge: kleine Modelle können sonst bis zum Timeout weiterschreiben
        options = {"temperature": 0, "num_predict": 768, "repeat_penalty": 1.1}
        body = {"model": self.model, "stream": False, "format": schema, "options": options,
                "messages": [{"role": "system", "content": system + " Antworte ausschließlich mit JSON nach dem vorgegebenen Schema."},
                             {"role": "user", "content": prompt, **({"images": images} if images else {})}]}
        try:
            resp = await self._client.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise AIError(f"Lokale KI nicht erreichbar ({type(exc).__name__}) – läuft der Ollama-Dienst?") from exc
        if resp.status_code == 404:
            raise AIError(f"Modell {self.model} ist noch nicht geladen – bitte in einigen Minuten erneut versuchen")
        if resp.status_code >= 400:
            raise AIError(f"Lokale KI meldet Fehler {resp.status_code}: {resp.text[:200]}")
        try:
            return cast(dict[str, Any], json.loads(resp.json()["message"]["content"]))
        except (KeyError, ValueError) as exc:
            raise AIError("Antwort der lokalen KI ist kein gültiges JSON") from exc


class AnthropicProvider:
    kind = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0, max_retries=2)
        self.model = model

    @property
    def label(self) -> str:
        return f"Claude ({self.model})"

    async def status(self) -> tuple[bool, str | None]:
        return True, None

    async def complete_json(self, *, system: str, text: str, file: tuple[bytes, str] | None, schema: dict[str, Any]) -> dict[str, Any]:
        from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam

        anthropic = self._anthropic
        content: list[dict[str, Any]] = []
        if file is not None:
            data, mime = file
            b64 = base64.b64encode(data).decode("ascii")
            if mime == "application/pdf":
                content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
            elif mime in ("image/png", "image/jpeg"):
                content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        content.append({"type": "text", "text": text})
        messages = cast("list[BetaMessageParam]", [{"role": "user", "content": content}])
        output_config: BetaOutputConfigParam = {"effort": "medium", "format": {"type": "json_schema", "schema": schema}}
        try:
            response = await self.client.beta.messages.create(
                model=self.model, max_tokens=4000, system=system, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config=output_config, messages=messages)
        except anthropic.AuthenticationError as exc:
            raise AIError("Anthropic-API-Schlüssel ungültig") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("KI-Dienst ausgelastet – bitte später erneut versuchen") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"KI-Dienst meldet Fehler {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("KI-Dienst nicht erreichbar") from exc
        if response.stop_reason in ("refusal", "max_tokens"):
            raise AIError("Die KI konnte das Dokument nicht verarbeiten – bitte manuell erfassen")
        block = next((b for b in response.content if b.type == "text"), None)
        if block is None:
            raise AIError("Keine Antwort der KI")
        try:
            return cast(dict[str, Any], json.loads(block.text))
        except ValueError as exc:
            raise AIError("Antwort der KI nicht lesbar") from exc


def build_provider(settings: Any) -> AIProvider | None:
    kind = settings.ai_provider
    if kind == "auto":
        kind = "ollama" if settings.ollama_url else "anthropic" if settings.anthropic_api_key.get_secret_value() else "none"
    if kind == "ollama" and settings.ollama_url:
        return OllamaProvider(settings.ollama_url, settings.ollama_model)
    if kind == "anthropic" and settings.anthropic_api_key.get_secret_value():
        return AnthropicProvider(settings.anthropic_api_key.get_secret_value(), settings.extraction_model)
    return None
