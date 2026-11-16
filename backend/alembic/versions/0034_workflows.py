"""Eigene Workflows: Auslöser (Cron, Ereignis, manuell), Blöcke als Kette, Ausführungsprotokoll.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE workflows (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        name          TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        description   TEXT,
        enabled       BOOLEAN NOT NULL DEFAULT false,
        trigger_type  TEXT NOT NULL CHECK (trigger_type IN ('cron', 'event', 'manual')),
        trigger_value TEXT,
        steps         JSONB NOT NULL DEFAULT '[]'::jsonb,
        last_cron_at  TIMESTAMPTZ,
        created_by    TEXT NOT NULL,
        updated_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE workflow_runs (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        workflow_id  UUID NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
        trigger      TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'stopped', 'failed')),
        input        JSONB NOT NULL DEFAULT '{}'::jsonb,
        log          JSONB NOT NULL DEFAULT '[]'::jsonb,
        error        TEXT,
        queued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at   TIMESTAMPTZ,
        finished_at  TIMESTAMPTZ
    )
    """,
    "CREATE INDEX workflow_runs_queue_idx ON workflow_runs (queued_at) WHERE status = 'queued'",
    "CREATE INDEX workflow_runs_wf_idx ON workflow_runs (workflow_id, queued_at DESC)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
