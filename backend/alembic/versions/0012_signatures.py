"""Digitale Signatur über Documenso (Phase 2d).

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE signature_requests (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id         UUID NOT NULL,
        subject_type      TEXT NOT NULL CHECK (subject_type IN ('offer', 'contract')),
        subject_id        UUID NOT NULL,
        title             TEXT NOT NULL,
        signer_name       TEXT NOT NULL,
        signer_email      TEXT NOT NULL,
        countersigner_email TEXT,
        documenso_id      BIGINT UNIQUE,
        status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'rejected', 'cancelled')),
        original_pdf      BYTEA NOT NULL,
        original_sha256   TEXT NOT NULL,
        signed_pdf        BYTEA,
        signed_sha256     TEXT,
        ledger_record_id  UUID,
        last_event        TEXT,
        created_by        TEXT NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at      TIMESTAMPTZ
    )
    """,
    "CREATE INDEX signature_requests_subject_idx ON signature_requests (subject_type, subject_id)",
    # Höchstens eine offene Signaturanfrage je Vorgang
    "CREATE UNIQUE INDEX signature_requests_one_pending ON signature_requests (subject_type, subject_id) WHERE status = 'pending'",
    """
    CREATE FUNCTION guard_signature() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Signaturvorgänge dürfen nicht gelöscht werden' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.status <> 'pending' AND (to_jsonb(NEW) - 'last_event') IS DISTINCT FROM (to_jsonb(OLD) - 'last_event') THEN
            RAISE EXCEPTION 'Abgeschlossener Signaturvorgang ist unveränderlich' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.original_pdf IS DISTINCT FROM OLD.original_pdf OR NEW.original_sha256 IS DISTINCT FROM OLD.original_sha256 THEN
            RAISE EXCEPTION 'Originaldokument ist unveränderlich' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER signature_requests_guard BEFORE UPDATE OR DELETE ON signature_requests FOR EACH ROW EXECUTE FUNCTION guard_signature()",
    "REVOKE TRUNCATE ON signature_requests FROM selbstlite_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
