"""Jahresabschluss-Vorbereitung: Abschlussbuchungen (Rückstellungen, Rechnungsabgrenzung) je Geschäftsjahr.

Revision ID: 0025
Revises: 0024
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE closing_entries (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          UUID NOT NULL,
        year               INTEGER NOT NULL,
        kind               TEXT NOT NULL CHECK (kind IN ('tax_provision', 'closing_costs', 'other_provision', 'prepaid', 'deferred')),
        amount             NUMERIC(19,2) NOT NULL CHECK (amount > 0),
        account            TEXT CHECK (account IS NULL OR account ~ '^[0-9]{4}$'),
        description        TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
        journal_entry_id   UUID NOT NULL REFERENCES journal_entries (id),
        reversal_entry_id  UUID REFERENCES journal_entries (id),
        created_by         TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX closing_entries_year_idx ON closing_entries (tenant_id, year)",
    """
    CREATE FUNCTION guard_closing_entry() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Abschlussbuchungen sind unveränderlich – Korrektur per Gegenbuchung' USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    "CREATE TRIGGER closing_entries_immutable BEFORE UPDATE OR DELETE ON closing_entries "
    "FOR EACH ROW EXECUTE FUNCTION guard_closing_entry()",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
