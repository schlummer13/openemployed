"""Revisionssicheres Dokumentenarchiv: unveränderliche Ablage mit Hash-Kette, Aufbewahrungsfristen und Volltextsuche.

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE archive_documents (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        seq             BIGINT NOT NULL,
        number          TEXT NOT NULL,
        category        TEXT NOT NULL CHECK (category IN ('invoice_out', 'receipt', 'contract', 'offer', 'mail', 'payroll', 'bank',
                                                          'tax', 'mandate', 'correspondence', 'annual', 'other')),
        title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
        document_date   DATE NOT NULL,
        retention_years INTEGER NOT NULL CHECK (retention_years BETWEEN 0 AND 30),
        retain_until    DATE NOT NULL,
        filename        TEXT NOT NULL,
        mime_type       TEXT NOT NULL,
        size            INTEGER NOT NULL,
        sha256          TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        chain_hash      TEXT NOT NULL CHECK (chain_hash ~ '^[0-9a-f]{64}$'),
        content         BYTEA,
        text_content    TEXT,
        source_type     TEXT,
        source_id       TEXT,
        party           TEXT,
        tags            TEXT[] NOT NULL DEFAULT '{}',
        notes           TEXT,
        purged_at       TIMESTAMPTZ,
        purged_by       TEXT,
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        search          tsvector GENERATED ALWAYS AS (to_tsvector('german', coalesce(title, '') || ' ' || coalesce(party, '') || ' ' ||
                                                     coalesce(number, '') || ' ' || coalesce(text_content, ''))) STORED,
        UNIQUE (tenant_id, seq),
        UNIQUE (tenant_id, number)
    )
    """,
    "CREATE UNIQUE INDEX archive_source_idx ON archive_documents (tenant_id, source_type, source_id, sha256) WHERE source_type IS NOT NULL",
    "CREATE INDEX archive_search_idx ON archive_documents USING gin (search)",
    "CREATE INDEX archive_date_idx ON archive_documents (tenant_id, document_date DESC)",
    # Inhalt, Hashes, Datum und Frist sind unveränderlich; Löschen nur als „Vernichtung“ nach Fristablauf (Inhalt → NULL)
    """
    CREATE FUNCTION guard_archive() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Archivdokumente können nicht gelöscht werden' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.sha256 <> OLD.sha256 OR NEW.chain_hash <> OLD.chain_hash OR NEW.seq <> OLD.seq OR NEW.number <> OLD.number
           OR NEW.document_date <> OLD.document_date OR NEW.retain_until <> OLD.retain_until OR NEW.category <> OLD.category
           OR NEW.filename <> OLD.filename OR NEW.created_at <> OLD.created_at OR NEW.tenant_id <> OLD.tenant_id THEN
            RAISE EXCEPTION 'Archivdokumente sind unveränderlich' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.content IS DISTINCT FROM OLD.content THEN
            IF NEW.content IS NOT NULL OR OLD.purged_at IS NOT NULL OR current_date <= OLD.retain_until THEN
                RAISE EXCEPTION 'Vernichtung erst nach Ablauf der Aufbewahrungsfrist' USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER archive_guard BEFORE UPDATE OR DELETE ON archive_documents FOR EACH ROW EXECUTE FUNCTION guard_archive()",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
