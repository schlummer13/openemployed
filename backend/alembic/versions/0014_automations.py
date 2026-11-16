"""Automatisierungen: Schalter, Laufprotokoll, Belegpostfach.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE automation_settings (
        tenant_id  UUID NOT NULL,
        key        TEXT NOT NULL,
        enabled    BOOLEAN NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, key)
    )
    """,
    """
    CREATE TABLE automation_runs (
        id          BIGSERIAL PRIMARY KEY,
        tenant_id   UUID NOT NULL,
        key         TEXT NOT NULL,
        trigger     TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual')),
        started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'skipped', 'error')),
        summary     TEXT,
        details     JSONB,
        actor       TEXT NOT NULL
    )
    """,
    "CREATE INDEX automation_runs_key_idx ON automation_runs (tenant_id, key, started_at DESC)",
    """
    CREATE TABLE receipt_inbox_messages (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        message_id  TEXT NOT NULL,
        sender      TEXT,
        subject     TEXT,
        received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        files       INT NOT NULL DEFAULT 0,
        drafts      INT NOT NULL DEFAULT 0,
        note        TEXT,
        UNIQUE (tenant_id, message_id)
    )
    """,
    # Entwürfe aus dem Postfach erkennbar machen
    "ALTER TABLE receipts ADD COLUMN source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'inbox', 'upload_ai'))",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
