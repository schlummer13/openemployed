"""Documenso-Anbindung: automatisch erzeugte Zugangsdaten, Abgleich von in Documenso angelegten Dokumenten und Vorlagen.

Revision ID: 0045
Revises: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE integration_secrets (
        key         TEXT PRIMARY KEY,
        value_enc   TEXT NOT NULL,
        meta        JSONB NOT NULL DEFAULT '{}',
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE sign_documents ALTER COLUMN content DROP NOT NULL, ALTER COLUMN sha256 DROP NOT NULL, "
    "ADD COLUMN source TEXT NOT NULL DEFAULT 'upload' CHECK (source IN ('upload', 'documenso', 'template')), "
    "ADD COLUMN documenso_id INTEGER, ADD COLUMN documenso_status TEXT, ADD COLUMN template_id INTEGER, "
    "ADD COLUMN recipients JSONB NOT NULL DEFAULT '[]', ADD COLUMN employee_id UUID REFERENCES employees (id), "
    "ADD COLUMN synced_at TIMESTAMPTZ",
    "CREATE UNIQUE INDEX sign_documents_documenso_idx ON sign_documents (tenant_id, documenso_id) WHERE documenso_id IS NOT NULL",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
