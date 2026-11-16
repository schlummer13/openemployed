"""JSON-Ausgabe für die API: Geldbeträge immer als exakter Dezimal-Text (nie Gleitkomma)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder


def _exact(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dt.time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, Mapping):
        return {k: _exact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_exact(v) for v in value]
    return value


def money_json(value: Any) -> Any:
    """Wie jsonable_encoder, aber Decimal → \"123.45\" (statt 123.45 als float, das z. B. 33.6 verfälschen würde)."""
    return jsonable_encoder(_exact(value))
