"""Kundenportal mit E-Mail-Code, Kündigungs-/Widerrufseingang (Phase 2c).

Revision ID: 0011
Revises: 0010

Codes und Sitzungstoken werden nur als HMAC-SHA-256 (Server-Pepper) gespeichert. Kündigungen und Widerrufe
(§ 312k BGB, § 355 BGB) werden mit Eingangszeitpunkt unveränderlich protokolliert; die Bearbeitung ist ein
separater Statuswechsel.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE portal_users (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        customer_id UUID NOT NULL REFERENCES customers (id),
        email       TEXT NOT NULL CHECK (email = lower(email) AND position('@' in email) > 1),
        status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_login_at TIMESTAMPTZ,
        UNIQUE (tenant_id, customer_id, email)
    )
    """,
    "CREATE INDEX portal_users_email_idx ON portal_users (tenant_id, email) WHERE status = 'active'",
    """
    CREATE TABLE portal_login_codes (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        email       TEXT NOT NULL,
        code_hash   TEXT,
        expires_at  TIMESTAMPTZ NOT NULL,
        attempts    INT NOT NULL DEFAULT 0,
        used_at     TIMESTAMPTZ,
        invalidated_at TIMESTAMPTZ,
        ip          TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX portal_login_codes_email_idx ON portal_login_codes (tenant_id, email, created_at DESC)",
    "CREATE INDEX portal_login_codes_ip_idx ON portal_login_codes (ip, created_at DESC)",
    """
    CREATE TABLE portal_login_failures (
        id         BIGSERIAL PRIMARY KEY,
        tenant_id  UUID NOT NULL,
        email      TEXT NOT NULL,
        ip         TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX portal_login_failures_idx ON portal_login_failures (tenant_id, email, created_at DESC)",
    """
    CREATE TABLE portal_sessions (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        token_hash   TEXT NOT NULL UNIQUE,
        email        TEXT NOT NULL,
        customer_id  UUID REFERENCES customers (id),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        remember     BOOLEAN NOT NULL DEFAULT false,
        ip           TEXT,
        user_agent   TEXT,
        revoked_at   TIMESTAMPTZ
    )
    """,
    "CREATE INDEX portal_sessions_email_idx ON portal_sessions (tenant_id, email) WHERE revoked_at IS NULL",
    """
    CREATE TABLE portal_events (
        id          BIGSERIAL PRIMARY KEY,
        tenant_id   UUID NOT NULL,
        email       TEXT,
        customer_id UUID,
        type        TEXT NOT NULL,
        subject_id  UUID,
        ip          TEXT,
        user_agent  TEXT,
        data        JSONB,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE SEQUENCE termination_number_seq START 1",
    """
    CREATE TABLE contract_terminations (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        number          TEXT NOT NULL DEFAULT ('KE-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('termination_number_seq')::text, 4, '0')),
        kind            TEXT NOT NULL CHECK (kind IN ('termination', 'withdrawal')),
        channel         TEXT NOT NULL CHECK (channel IN ('portal', 'public', 'manual')),
        termination_type TEXT NOT NULL DEFAULT 'ordinary' CHECK (termination_type IN ('ordinary', 'extraordinary')),
        contract_id     UUID REFERENCES contracts (id),
        customer_id     UUID REFERENCES customers (id),
        name            TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 200),
        email           TEXT NOT NULL,
        customer_number TEXT,
        contract_reference TEXT,
        requested_date  DATE,
        reason          TEXT CHECK (reason IS NULL OR length(reason) <= 2000),
        received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        ip              TEXT,
        user_agent      TEXT,
        confirmation_sent_at TIMESTAMPTZ,
        status          TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'processed', 'rejected')),
        processed_by    TEXT,
        processed_at    TIMESTAMPTZ,
        effective_date  DATE,
        processing_note TEXT,
        UNIQUE (tenant_id, number)
    )
    """,
    """
    CREATE FUNCTION guard_termination() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Kündigungseingänge dürfen nicht gelöscht werden' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['confirmation_sent_at','status','processed_by','processed_at','effective_date','processing_note','contract_id','customer_id'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['confirmation_sent_at','status','processed_by','processed_at','effective_date','processing_note','contract_id','customer_id']) THEN
            RAISE EXCEPTION 'Eingang % ist unveränderlich', OLD.number USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER contract_terminations_guard BEFORE UPDATE OR DELETE ON contract_terminations FOR EACH ROW EXECUTE FUNCTION guard_termination()",
    "REVOKE TRUNCATE ON contract_terminations, portal_events FROM openemployed_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
