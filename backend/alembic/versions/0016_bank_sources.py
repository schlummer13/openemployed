"""Allgemeine Bankschnittstelle: Dateiimport (CAMT.053/MT940/CSV) und FinTS.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE bank_transactions DROP CONSTRAINT bank_transactions_source_check",
    "ALTER TABLE bank_transactions ADD CONSTRAINT bank_transactions_source_check CHECK (source IN ('qonto', 'csv', 'file', 'fints'))",
    "ALTER TABLE bank_transactions ADD COLUMN account_iban TEXT",
    """
    CREATE TABLE bank_connections (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        label       TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 100),
        kind        TEXT NOT NULL CHECK (kind IN ('fints', 'qonto', 'file')),
        bank_code   TEXT CHECK (bank_code IS NULL OR bank_code ~ '^[0-9]{8}$'),
        server_url  TEXT CHECK (server_url IS NULL OR server_url ~ '^https://'),
        login_name  TEXT,
        iban        TEXT,
        last_sync_at TIMESTAMPTZ,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
