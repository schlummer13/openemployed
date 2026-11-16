"""Aktive Firma (Mandant) je Anfrage. Gesetzt bei der Anmeldeprüfung (``current_user``), sonst die Stammfirma der Installation."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

COOKIE = "openemployed_company"
_current: ContextVar[UUID | None] = ContextVar("openemployed_company", default=None)


def set_current(company_id: UUID | None) -> None:
    _current.set(company_id)


def current() -> UUID:
    from app.core.config import get_settings

    return _current.get() or get_settings().tenant_id
