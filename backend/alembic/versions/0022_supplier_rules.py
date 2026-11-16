"""Lieferanten-Regeln: Standardkonto und Status „einsortiert“; Belege merken sich ihr Aufwands-/Anlagekonto.

Revision ID: 0022
Revises: 0021

Ein neuer Lieferant aus dem Belegpostfach wird mit needs_review = true angelegt und erst nach der ersten manuellen
Einsortierung automatisch kontiert. Gebuchte Belege bleiben unveränderlich (Trigger guard_booked_receipt).
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE suppliers ADD COLUMN default_account TEXT CHECK (default_account IS NULL OR default_account ~ '^[0-9]{4}$')",
    "ALTER TABLE suppliers ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE receipts ADD COLUMN expense_account TEXT CHECK (expense_account IS NULL OR expense_account ~ '^[0-9]{4}$')",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
