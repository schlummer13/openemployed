"""Einrichtungs-Checkliste: manuell bestätigte Schritte (z. B. Verfahrensdokumentation freigegeben).

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE setup_flags (
        tenant_id UUID NOT NULL,
        key       TEXT NOT NULL CHECK (key ~ '^[a-z_]{2,40}$'),
        done_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        done_by   TEXT NOT NULL,
        PRIMARY KEY (tenant_id, key)
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
