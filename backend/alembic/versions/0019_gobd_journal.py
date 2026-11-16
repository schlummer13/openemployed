"""GoBD-Festschreibung des Journals.

Revision ID: 0019
Revises: 0018

- Belegdatum (``document_date``) getrennt vom Buchungsdatum (``entry_date``): Fällt ein Vorgang in eine bereits
  festgeschriebene Periode, wird er mit Originalbelegdatum in die nächste offene Periode gebucht (GoBD Rz. 58 ff.).
- Jeder Buchungssatz trägt Ledger-Datensatz und SHA-256-Hash; die Buchungsnummer vergibt der Ledger lückenlos.
- Datenbanksperre: Buchungssätze mit Buchungsdatum in einer gesperrten Periode werden abgewiesen (zusätzlich zum Ledger).
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE journal_entries ADD COLUMN document_date DATE",
    "ALTER TABLE journal_entries ADD COLUMN ledger_hash TEXT",
    """
    CREATE FUNCTION guard_journal_period() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE locked BOOLEAN := false;
    BEGIN
        -- Ledger-Schema liegt in derselben Datenbank (Produktion); Tests ohne Ledger-Schema prüfen über den Ledger-Dienst
        IF to_regclass('ledger.locked_periods') IS NOT NULL THEN
            EXECUTE 'SELECT EXISTS (SELECT 1 FROM ledger.locked_periods WHERE tenant_id = $1 AND period = $2)'
                INTO locked USING NEW.tenant_id, to_char(NEW.entry_date, 'YYYY-MM');
        END IF;
        IF locked THEN
            RAISE EXCEPTION 'Periode % ist festgeschrieben – Buchung nur in eine offene Periode', to_char(NEW.entry_date, 'YYYY-MM')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER journal_entries_period_guard BEFORE INSERT ON journal_entries FOR EACH ROW EXECUTE FUNCTION guard_journal_period()",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
