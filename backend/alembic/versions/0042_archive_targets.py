"""Archiv-Spiegelung: externe Ziele (S3 mit Object Lock/WORM, WebDAV, Ordner/Festplatte) und Kopie-Nachweise je Dokument.

Revision ID: 0042
Revises: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE archive_targets (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        name          TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
        kind          TEXT NOT NULL CHECK (kind IN ('s3', 'webdav', 'folder')),
        config        JSONB NOT NULL DEFAULT '{}',
        secret_enc    TEXT,
        object_lock   BOOLEAN NOT NULL DEFAULT false,
        lock_mode     TEXT NOT NULL DEFAULT 'COMPLIANCE' CHECK (lock_mode IN ('COMPLIANCE', 'GOVERNANCE')),
        schedule      TEXT NOT NULL DEFAULT 'daily' CHECK (schedule IN ('daily', 'weekly', 'manual')),
        enabled       BOOLEAN NOT NULL DEFAULT true,
        last_run_at   TIMESTAMPTZ,
        last_status   TEXT,
        last_message  TEXT,
        last_verified_at TIMESTAMPTZ,
        created_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE archive_replicas (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        target_id     UUID NOT NULL REFERENCES archive_targets (id) ON DELETE CASCADE,
        document_id   UUID NOT NULL REFERENCES archive_documents (id),
        location      TEXT NOT NULL,
        sha256        TEXT NOT NULL,
        md5           TEXT,
        size          INTEGER NOT NULL,
        locked_until  DATE,
        status        TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error', 'missing', 'purged')),
        error         TEXT,
        replicated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        verified_at   TIMESTAMPTZ,
        UNIQUE (target_id, document_id)
    )
    """,
    "CREATE INDEX archive_replicas_target_idx ON archive_replicas (target_id, status)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE archive_replicas")
    op.execute("DROP TABLE archive_targets")
