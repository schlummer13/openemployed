"""Eigene Rollen mit Rechten je Bereich; Standardrollen anpassbar (außer Geschäftsführung).

Revision ID: 0038
Revises: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE roles (
        key          TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
        label        TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
        description  TEXT,
        permissions  JSONB NOT NULL,
        updated_by   TEXT NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE users DROP CONSTRAINT users_role_check",
    "ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role ~ '^[a-z][a-z0-9_]{1,39}$')",
    "ALTER TABLE company_members DROP CONSTRAINT company_members_role_check",
    "ALTER TABLE company_members ADD CONSTRAINT company_members_role_check CHECK (role ~ '^[a-z][a-z0-9_]{1,39}$')",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
