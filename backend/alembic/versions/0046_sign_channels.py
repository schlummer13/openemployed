"""Versandwege für Verträge: Documenso, qualifizierte Signatur über externen Anbieter, Papier, E-Mail mit Rücklauf.

Revision ID: 0046
Revises: 0045
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE sign_documents DROP CONSTRAINT IF EXISTS sign_documents_status_check",
    "ALTER TABLE sign_documents ADD CONSTRAINT sign_documents_status_check "
    "CHECK (status IN ('draft', 'pending', 'awaiting_paper', 'signed', 'rejected'))",
    "ALTER TABLE sign_documents ADD COLUMN channel TEXT CHECK (channel IN ('documenso', 'qes', 'paper', 'email')), "
    "ADD COLUMN qes_provider TEXT, ADD COLUMN qes_request_id TEXT, ADD COLUMN qes_document_id TEXT, "
    "ADD COLUMN mailed_to TEXT, ADD COLUMN mailed_at TIMESTAMPTZ, ADD COLUMN paper_note TEXT, "
    "ADD COLUMN personnel_document_id UUID REFERENCES personnel_documents (id)",
    "UPDATE sign_documents SET channel = 'documenso' WHERE status <> 'draft' AND channel IS NULL",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
