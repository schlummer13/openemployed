"""E-Rechnungs-Dokumente (ZUGFeRD-PDF + XML), Firmenlogo, Versandprotokoll (Phase 1b).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE company_logos (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        content     BYTEA NOT NULL CHECK (length(content) BETWEEN 1 AND 1048576),
        mime_type   TEXT NOT NULL CHECK (mime_type IN ('image/png', 'image/jpeg', 'image/svg+xml')),
        sha256      TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        active      BOOLEAN NOT NULL DEFAULT true,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by  TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX company_logos_one_active ON company_logos (tenant_id) WHERE active",
    # Archiv der erzeugten Dokumente: unveränderlich (GoBD), Hashes zusätzlich im Ledger festgeschrieben
    """
    CREATE TABLE invoice_documents (
        invoice_id  UUID PRIMARY KEY REFERENCES invoices (id),
        pdf         BYTEA NOT NULL,
        xml         BYTEA NOT NULL,
        pdf_sha256  TEXT NOT NULL CHECK (pdf_sha256 ~ '^[0-9a-f]{64}$'),
        xml_sha256  TEXT NOT NULL CHECK (xml_sha256 ~ '^[0-9a-f]{64}$'),
        format      TEXT NOT NULL,
        ledger_record_id UUID NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TRIGGER invoice_documents_immutable BEFORE UPDATE OR DELETE ON invoice_documents
        FOR EACH ROW EXECUTE FUNCTION forbid_modification()
    """,
    """
    CREATE TRIGGER invoice_documents_no_truncate BEFORE TRUNCATE ON invoice_documents
        FOR EACH STATEMENT EXECUTE FUNCTION forbid_modification()
    """,
    # Versandprotokoll (Nachweis des Zugangs, z. B. für den Verzugsbeginn)
    """
    CREATE TABLE invoice_deliveries (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        invoice_id  UUID NOT NULL REFERENCES invoices (id),
        channel     TEXT NOT NULL CHECK (channel IN ('email')),
        recipient   TEXT NOT NULL,
        message_id  TEXT NOT NULL,
        sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        sent_by     TEXT NOT NULL
    )
    """,
    "CREATE INDEX invoice_deliveries_invoice_idx ON invoice_deliveries (invoice_id)",
    """
    CREATE TRIGGER invoice_deliveries_immutable BEFORE UPDATE OR DELETE ON invoice_deliveries
        FOR EACH ROW EXECUTE FUNCTION forbid_modification()
    """,
    "REVOKE UPDATE, DELETE, TRUNCATE ON invoice_documents, invoice_deliveries FROM openemployed_app",
    "REVOKE DELETE, TRUNCATE ON company_logos FROM openemployed_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar (Aufbewahrungspflicht)")
