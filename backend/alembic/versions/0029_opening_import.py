"""Eröffnungsbilanz (Saldenvorträge, offene Posten) und Protokoll der Datenimporte.

Revision ID: 0029
Revises: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    "('SKR03', '9000', 'Saldenvorträge Sachkonten', 'equity'), ('SKR04', '9000', 'Saldenvorträge Sachkonten', 'equity'), "
    "('SKR03', '9008', 'Saldenvorträge Debitoren', 'equity'), ('SKR04', '9008', 'Saldenvorträge Debitoren', 'equity'), "
    "('SKR03', '9009', 'Saldenvorträge Kreditoren', 'equity'), ('SKR04', '9009', 'Saldenvorträge Kreditoren', 'equity') "
    "ON CONFLICT (chart, number) DO NOTHING",
    """
    CREATE TABLE opening_items (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        UUID NOT NULL,
        kind             TEXT NOT NULL CHECK (kind IN ('receivable', 'payable')),
        party            TEXT NOT NULL CHECK (length(party) BETWEEN 1 AND 200),
        document_number  TEXT NOT NULL CHECK (length(document_number) BETWEEN 1 AND 100),
        document_date    DATE NOT NULL,
        due_date         DATE,
        amount           NUMERIC(19,2) NOT NULL CHECK (amount > 0),
        journal_entry_id UUID REFERENCES journal_entries (id),
        paid_on          DATE,
        payment_entry_id UUID REFERENCES journal_entries (id),
        created_by       TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, kind, document_number)
    )
    """,
    """
    CREATE TABLE data_imports (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('datev', 'customers', 'suppliers', 'opening')),
        filename    TEXT,
        sha256      TEXT NOT NULL,
        rows        INTEGER NOT NULL,
        summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, kind, sha256)
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
