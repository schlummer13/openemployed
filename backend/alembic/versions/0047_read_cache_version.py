"""Gemeinsamer Änderungszähler für den Lese-Zwischenspeicher (über alle Server-Prozesse).

Jede schreibende API-Anfrage zählt ihn vor der Antwort hoch – auch Änderungen ohne Audit-Eintrag (z. B. Kontostand)
machen zwischengespeicherte Übersichten damit sofort ungültig.

Revision ID: 0047
Revises: 0046
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "CREATE SEQUENCE IF NOT EXISTS read_cache_version_seq START 1",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS read_cache_version_seq")
