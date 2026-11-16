"""Signatur beliebiger Verträge (Lieferanten, Vermieter, NDA, Partner) über Documenso, mit Formprüfung und Archivierung.

Revision ID: 0044
Revises: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE sign_documents (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        kind            TEXT NOT NULL,
        counterparty    TEXT NOT NULL,
        signer_name     TEXT NOT NULL,
        signer_email    TEXT NOT NULL,
        filename        TEXT NOT NULL,
        sha256          TEXT NOT NULL,
        content         BYTEA NOT NULL,
        status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'pending', 'signed', 'rejected')),
        form_level      TEXT NOT NULL,
        form_message    TEXT NOT NULL,
        form_acknowledged_by TEXT,
        signed_content  BYTEA,
        signed_sha256   TEXT,
        archive_document_id UUID,
        notes           TEXT,
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at    TIMESTAMPTZ
    )
    """,
    "CREATE INDEX sign_documents_tenant_idx ON sign_documents (tenant_id, created_at DESC)",
    "ALTER TABLE signature_requests DROP CONSTRAINT IF EXISTS signature_requests_subject_type_check",
    "ALTER TABLE signature_requests ADD CONSTRAINT signature_requests_subject_type_check "
    "CHECK (subject_type IN ('offer', 'contract', 'personnel_document', 'document'))",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
