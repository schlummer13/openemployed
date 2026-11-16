"""Steuervorauszahlungen (KSt, GewSt, USt-Sondervorauszahlung), Steuereinstellungen und Monatsabschlüsse.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    "('SKR03', '1781', 'Umsatzsteuer-Vorauszahlungen 1/11', 'liability'), ('SKR04', '3830', 'Umsatzsteuer-Vorauszahlungen 1/11', 'liability') "
    "ON CONFLICT (chart, number) DO NOTHING",
    """
    CREATE TABLE tax_settings (
        tenant_id      UUID PRIMARY KEY,
        vat_extension  BOOLEAN,
        updated_by     TEXT NOT NULL,
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE tax_prepayments (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        UUID NOT NULL,
        tax              TEXT NOT NULL CHECK (tax IN ('kst', 'gewst', 'ust_svz')),
        year             INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2100),
        due_date         DATE NOT NULL,
        amount           NUMERIC(19,2) NOT NULL CHECK (amount >= 0),
        soli             NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (soli >= 0),
        note             TEXT,
        paid_on          DATE,
        journal_entry_id UUID REFERENCES journal_entries (id),
        created_by       TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, tax, due_date)
    )
    """,
    """
    CREATE TABLE month_closings (
        tenant_id    UUID NOT NULL,
        period       TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
        confirmed    JSONB NOT NULL DEFAULT '[]'::jsonb,
        ledger_hash  TEXT,
        closed_by    TEXT NOT NULL,
        closed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, period)
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
