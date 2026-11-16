"""SEPA: Lastschriftmandate, Gläubiger-ID, Bankverbindung der Lieferanten, Protokoll erzeugter Zahlungsdateien.

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE suppliers ADD COLUMN iban TEXT, ADD COLUMN bic TEXT",
    """
    CREATE TABLE sepa_settings (
        tenant_id     UUID PRIMARY KEY,
        creditor_id   TEXT,
        batch_booking BOOLEAN NOT NULL DEFAULT true,
        updated_by    TEXT NOT NULL,
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE sepa_mandates (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      UUID NOT NULL,
        customer_id    UUID NOT NULL REFERENCES customers (id),
        reference      TEXT NOT NULL CHECK (reference ~ '^[A-Za-z0-9+?/:().,'' -]{1,35}$'),
        scheme         TEXT NOT NULL DEFAULT 'CORE' CHECK (scheme IN ('CORE', 'B2B')),
        signed_on      DATE NOT NULL,
        account_holder TEXT NOT NULL,
        iban           TEXT NOT NULL,
        bic            TEXT,
        recurring      BOOLEAN NOT NULL DEFAULT true,
        first_used_on  DATE,
        last_used_on   DATE,
        revoked_on     DATE,
        created_by     TEXT NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, reference)
    )
    """,
    "CREATE UNIQUE INDEX sepa_mandates_active_idx ON sepa_mandates (customer_id) WHERE revoked_on IS NULL",
    """
    CREATE TABLE sepa_files (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        kind          TEXT NOT NULL CHECK (kind IN ('transfer', 'direct_debit')),
        purpose       TEXT NOT NULL CHECK (purpose IN ('suppliers', 'salaries', 'customers')),
        msg_id        TEXT NOT NULL,
        due_date      DATE NOT NULL,
        count         INTEGER NOT NULL,
        total         NUMERIC(19,2) NOT NULL,
        items         JSONB NOT NULL,
        xml           TEXT NOT NULL,
        created_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, msg_id)
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
