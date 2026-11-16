"""Voranmeldungszeitraum je Rechnung und Beleg (Phase 1d).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14

``vat_period`` (JJJJ-MM) wird bei Freigabe bzw. Buchung einmalig gesetzt und ist danach – wie der übrige
Datensatz – unveränderlich. Liegt der eigentliche Monat in einer bereits gesperrten (gemeldeten) Periode, wird der
Vorgang dem nächsten offenen Monat zugeordnet (Nachmeldung statt Änderung einer abgegebenen UStVA).

Bestandsdaten werden einmalig nachgetragen; dafür werden die Schutz-Trigger in dieser Migration kurz deaktiviert.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE invoices ADD COLUMN vat_period TEXT CHECK (vat_period IS NULL OR vat_period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$')",
    "ALTER TABLE receipts ADD COLUMN vat_period TEXT CHECK (vat_period IS NULL OR vat_period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$')",
    "ALTER TABLE invoices DISABLE TRIGGER invoices_guard",
    """
    UPDATE invoices SET vat_period = to_char(COALESCE(service_date_to, service_date_from, issue_date), 'YYYY-MM')
     WHERE status = 'issued' AND vat_period IS NULL
    """,
    "ALTER TABLE invoices ENABLE TRIGGER invoices_guard",
    "ALTER TABLE receipts DISABLE TRIGGER receipts_guard",
    "UPDATE receipts SET vat_period = to_char(receipt_date, 'YYYY-MM') WHERE status = 'booked' AND vat_period IS NULL",
    "ALTER TABLE receipts ENABLE TRIGGER receipts_guard",
    "ALTER TABLE invoices ADD CONSTRAINT invoices_vat_period_when_issued CHECK (status = 'draft' OR vat_period IS NOT NULL)",
    "ALTER TABLE receipts ADD CONSTRAINT receipts_vat_period_when_booked CHECK (status = 'draft' OR vat_period IS NOT NULL)",
    "CREATE INDEX invoices_vat_period_idx ON invoices (tenant_id, vat_period) WHERE status = 'issued'",
    "CREATE INDEX receipts_vat_period_idx ON receipts (tenant_id, vat_period) WHERE status = 'booked'",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
