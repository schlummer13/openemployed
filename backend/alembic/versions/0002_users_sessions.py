"""Benutzer (Google Workspace) und Sitzungen. Keine Rollen.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE users (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        google_sub    TEXT NOT NULL UNIQUE CHECK (length(google_sub) BETWEEN 1 AND 255),
        email         TEXT NOT NULL CHECK (email = lower(email) AND position('@' IN email) > 1),
        name          TEXT,
        picture_url   TEXT CHECK (picture_url IS NULL OR picture_url LIKE 'https://%'),
        active        BOOLEAN NOT NULL DEFAULT true,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_login_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX users_email_idx ON users (tenant_id, email)",
    """
    CREATE TABLE user_sessions (
        id           UUID PRIMARY KEY,
        token_hash   BYTEA NOT NULL UNIQUE CHECK (length(token_hash) = 32),
        user_id      UUID NOT NULL REFERENCES users (id),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        revoked_at   TIMESTAMPTZ,
        ip           INET,
        user_agent   TEXT,
        CHECK (expires_at > created_at)
    )
    """,
    "CREATE INDEX user_sessions_user_idx ON user_sessions (user_id) WHERE revoked_at IS NULL",
    # Benutzer werden nie gelöscht (Audit-Log verweist auf sie), nur gesperrt
    "REVOKE DELETE, TRUNCATE ON users FROM openemployed_app",
    "REVOKE TRUNCATE ON user_sessions FROM openemployed_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE user_sessions")
    op.execute("DROP TABLE users")
