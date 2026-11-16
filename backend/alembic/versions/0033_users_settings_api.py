"""Rollen und Anmeldeverfahren (OIDC, E-Mail + Passwort + TOTP), Einstellungen in der App, API-Schlüssel und Webhooks.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    # Bestehende Benutzer behalten volle Rechte (Geschäftsführung)
    "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'accounting', 'advisor', 'employee'))",
    "ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'google' CHECK (auth_provider IN ('google', 'microsoft', 'oidc', "
    "'password', 'dev'))",
    "ALTER TABLE users ADD COLUMN password_hash TEXT, ADD COLUMN totp_secret TEXT, ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT false, "
    "ADD COLUMN failed_logins INTEGER NOT NULL DEFAULT 0, ADD COLUMN locked_until TIMESTAMPTZ, ADD COLUMN invited_by TEXT, "
    "ADD COLUMN invite_token_hash BYTEA, ADD COLUMN invite_expires_at TIMESTAMPTZ",
    "UPDATE users SET auth_provider = 'dev' WHERE google_sub LIKE 'dev:%'",
    "CREATE UNIQUE INDEX users_tenant_email_uidx ON users (tenant_id, email)",
    """
    CREATE TABLE app_settings (
        tenant_id   UUID NOT NULL,
        key         TEXT NOT NULL CHECK (key ~ '^[a-z0-9_]{2,64}$'),
        value       JSONB,
        secret      TEXT,
        updated_by  TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, key)
    )
    """,
    """
    CREATE TABLE api_keys (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        prefix       TEXT NOT NULL UNIQUE,
        key_hash     BYTEA NOT NULL UNIQUE CHECK (length(key_hash) = 32),
        role         TEXT NOT NULL CHECK (role IN ('accounting', 'advisor')),
        created_by   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ,
        revoked_at   TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE webhook_endpoints (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        url         TEXT NOT NULL CHECK (url LIKE 'https://%' OR url LIKE 'http://%'),
        description TEXT,
        events      TEXT[] NOT NULL,
        secret      TEXT NOT NULL,
        active      BOOLEAN NOT NULL DEFAULT true,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE webhook_deliveries (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        endpoint_id   UUID NOT NULL REFERENCES webhook_endpoints (id) ON DELETE CASCADE,
        event         TEXT NOT NULL,
        payload       JSONB NOT NULL,
        status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
        attempts      INTEGER NOT NULL DEFAULT 0,
        next_attempt  TIMESTAMPTZ NOT NULL DEFAULT now(),
        response_code INTEGER,
        last_error    TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at  TIMESTAMPTZ
    )
    """,
    "CREATE INDEX webhook_deliveries_due_idx ON webhook_deliveries (next_attempt) WHERE status = 'pending'",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
