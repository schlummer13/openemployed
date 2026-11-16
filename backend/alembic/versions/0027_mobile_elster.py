"""Handy-Belege (Herkunft „mobile“) und ELSTER-Übertragungsprotokoll der Umsatzsteuer-Voranmeldung.

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE receipts DROP CONSTRAINT receipts_source_check",
    "ALTER TABLE receipts ADD CONSTRAINT receipts_source_check CHECK (source IN ('manual', 'inbox', 'upload_ai', 'mobile'))",
    """
    CREATE TABLE elster_submissions (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      UUID NOT NULL,
        period         TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2]|Q[1-4])$'),
        test_mode      BOOLEAN NOT NULL,
        status         TEXT NOT NULL CHECK (status IN ('validated', 'sent', 'error')),
        transfer_ticket TEXT,
        xml_sha256     TEXT NOT NULL,
        response       TEXT,
        error          TEXT,
        created_by     TEXT NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX elster_submissions_period_idx ON elster_submissions (tenant_id, period)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
