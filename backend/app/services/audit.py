"""Audit-Log (GoBD-Nachvollziehbarkeit): wer hat wann was geändert. Nur anhängen."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.ledger_client import to_ledger_payload


async def record(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: UUID | str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor, actor_type, action, entity_type, entity_id, before, after) "
            "VALUES (:tenant_id, :actor, 'user', :action, :entity_type, :entity_id, "
            "CAST(:before AS jsonb), CAST(:after AS jsonb))"
        ),
        {
            "tenant_id": tenant_id,
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "before": json.dumps(to_ledger_payload(before)) if before is not None else None,
            "after": json.dumps(to_ledger_payload(after)) if after is not None else None,
        },
    )
    from app.services import events

    await events.from_audit(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                            after=after)
