"""Onlinebanking über FinTS/HBCI (PIN/TAN) – für Banken ohne eigene API.

Die PIN wird bei jedem Abruf eingegeben und nur für die Dauer eines TAN-Dialogs (max. 5 Minuten) im Arbeitsspeicher
gehalten – nie gespeichert oder protokolliert. Unterstützt werden App-Freigabe (decoupled, z. B. pushTAN) und
manuelle TAN-Eingabe. Für den Produktivbetrieb verlangt die Kreditwirtschaft eine registrierte FinTS-Produkt-ID.
"""

from __future__ import annotations

import datetime as dt
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.bank import BankError
from app.services.bank_formats import _from_mt940

TTL_SECONDS = 300


@dataclass
class _Pending:
    params: dict[str, Any]
    client_data: bytes
    dialog_data: bytes
    tan_data: bytes
    stage: str            # "init" (Anmeldung) oder "transactions"
    expires: float = field(default_factory=lambda: time.monotonic() + TTL_SECONDS)


_PENDING: dict[str, _Pending] = {}


def _default_factory(params: dict[str, Any], from_data: bytes | None = None) -> Any:
    from fints.client import FinTS3PinTanClient

    return FinTS3PinTanClient(params["bank_code"], params["login"], params["pin"], params["server_url"],
                              product_id=params["product_id"], from_data=from_data)


ClientFactory = Callable[..., Any]


def _purge() -> None:
    now = time.monotonic()
    for k in [k for k, v in _PENDING.items() if v.expires < now]:
        _PENDING.pop(k, None)


def _choose_tan(client: Any) -> None:
    if not client.get_current_tan_mechanism():
        client.fetch_tan_mechanisms()
        mechs = [m for m in client.get_tan_mechanisms() if m != "999"]
        if mechs:
            client.set_tan_mechanism(mechs[0])
    if client.is_tan_media_required() and not client.selected_tan_medium:
        media = client.get_tan_media()
        if media and media[1]:
            client.set_tan_medium(media[1][0])


def _challenge(res: Any) -> dict[str, Any]:
    return {"challenge": getattr(res, "challenge", None) or "Bitte Auftrag freigeben", "decoupled": bool(getattr(res, "decoupled", False)),
            "challenge_html": getattr(res, "challenge_html", None)}


def _fetch(client: Any, params: dict[str, Any]) -> Any:
    accounts = client.get_sepa_accounts()
    if hasattr(accounts, "challenge"):
        return accounts
    account = next((a for a in accounts if a.iban.replace(" ", "") == params["iban"].replace(" ", "")), None)
    if account is None:
        raise BankError("Konto mit dieser IBAN wurde in der Bankverbindung nicht gefunden")
    return client.get_transactions(account, params["start"], params["end"])


def _rows(result: Any, iban: str) -> list[dict[str, Any]]:
    return [_from_mt940(t.data, iban, i) for i, t in enumerate(result or [])]


def start(params: dict[str, Any], factory: ClientFactory = _default_factory) -> dict[str, Any]:
    """Startet den Abruf. Ergebnis: ``{"status": "ok", "rows": [...]}`` oder ``{"status": "tan", "token": …, …}``."""
    from fints.client import NeedTANResponse

    _purge()
    if not params.get("product_id"):
        raise BankError("FinTS-Produkt-ID fehlt (FINTS_PRODUCT_ID) – bei der Deutschen Kreditwirtschaft registrieren")
    client = factory(params)
    _choose_tan(client)
    try:
        with client:
            init = getattr(client, "init_tan_response", None)
            res = init if isinstance(init, NeedTANResponse) else _fetch(client, params)
            stage = "init" if isinstance(init, NeedTANResponse) else "transactions"
            if isinstance(res, NeedTANResponse):
                token = secrets.token_urlsafe(24)
                _PENDING[token] = _Pending(params, client.deconstruct(including_private=True), client.pause_dialog(), res.get_data(), stage)
                return {"status": "tan", "token": token} | _challenge(res)
            return {"status": "ok", "rows": _rows(res, params["iban"])}
    except BankError:
        raise
    except Exception as exc:
        raise BankError(f"Bankverbindung fehlgeschlagen: {exc}") from exc


def proceed(token: str, tan: str, factory: ClientFactory = _default_factory) -> dict[str, Any]:
    from fints.client import NeedTANResponse

    _purge()
    pending = _PENDING.pop(token, None)
    if pending is None:
        raise BankError("TAN-Dialog abgelaufen – bitte den Abruf neu starten")
    client = factory(pending.params, from_data=pending.client_data)
    try:
        with client.resume_dialog(pending.dialog_data):
            res = client.send_tan(NeedTANResponse.from_data(pending.tan_data), tan)
            if pending.stage == "init" and not isinstance(res, NeedTANResponse):
                res = _fetch(client, pending.params)
            if isinstance(res, NeedTANResponse):
                new = secrets.token_urlsafe(24)
                _PENDING[new] = _Pending(pending.params, client.deconstruct(including_private=True), client.pause_dialog(), res.get_data(),
                                         "transactions")
                return {"status": "tan", "token": new} | _challenge(res)
            return {"status": "ok", "rows": _rows(res, pending.params["iban"])}
    except BankError:
        raise
    except Exception as exc:
        raise BankError(f"TAN-Freigabe fehlgeschlagen: {exc}") from exc


def default_range(today: dt.date, days: int = 30) -> tuple[dt.date, dt.date]:
    return today - dt.timedelta(days=days), today


