"""Qonto-Anbindung entfernt: Bankumsätze nur noch per FinTS (Onlinebanking) oder Kontoauszug-Import.

Revision ID: 0020
Revises: 0019

Frühere Migrationen bleiben als Historie unverändert; hier werden die zulässigen Werte bereinigt. Schlägt die Migration
fehl, existieren noch Datensätze mit Quelle „qonto“ – diese bleiben dann bewusst unangetastet (GoBD) und die Prüfung muss
mit dem Steuerberater geklärt werden.
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE payments DROP CONSTRAINT payments_source_check",
    "ALTER TABLE payments ADD CONSTRAINT payments_source_check CHECK (source IN ('manual', 'import'))",
    "ALTER TABLE bank_transactions DROP CONSTRAINT bank_transactions_source_check",
    "ALTER TABLE bank_transactions ADD CONSTRAINT bank_transactions_source_check CHECK (source IN ('csv', 'file', 'fints'))",
    "ALTER TABLE bank_balances DROP CONSTRAINT bank_balances_source_check",
    "ALTER TABLE bank_balances ADD CONSTRAINT bank_balances_source_check CHECK (source IN ('manual', 'camt', 'fints'))",
    "ALTER TABLE bank_connections DROP CONSTRAINT bank_connections_kind_check",
    "ALTER TABLE bank_connections ADD CONSTRAINT bank_connections_kind_check CHECK (kind IN ('fints', 'file'))",
    # Protokoll und Schalter des entfernten Qonto-Jobs
    "DELETE FROM automation_runs WHERE key = 'bank_sync'",
    "DELETE FROM automation_settings WHERE key = 'bank_sync'",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
