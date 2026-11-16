"""Mehrere Firmen (Mandanten) in einer Installation: Firmenliste und Zugriffsrechte je Firma.

Revision ID: 0037
Revises: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE companies (
        id          UUID PRIMARY KEY,
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        color       TEXT,
        archived_at TIMESTAMPTZ,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE company_members (
        company_id UUID NOT NULL REFERENCES companies (id),
        user_id    UUID NOT NULL REFERENCES users (id),
        role       TEXT NOT NULL CHECK (role IN ('admin', 'accounting', 'advisor', 'employee')),
        created_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (company_id, user_id)
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
