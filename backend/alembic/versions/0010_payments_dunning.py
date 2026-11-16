"""Zahlungseingänge und Mahnwesen (Phase 2b).

Revision ID: 0010
Revises: 0009

Zahlungen werden nie gelöscht oder geändert, sondern storniert (``reversed_at``), damit der Kontoabgleich
nachvollziehbar bleibt (GoBD). Mahnschreiben sind nach Erstellung unveränderlich (nur Versandvermerk).
Der Basiszinssatz (§ 247 BGB) wird als Tabelle gepflegt; Werte bitte halbjährlich mit der Bundesbank abgleichen.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE payments (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        invoice_id    UUID NOT NULL REFERENCES invoices (id),
        paid_on       DATE NOT NULL,
        amount        NUMERIC(19,2) NOT NULL CHECK (amount > 0),
        method        TEXT NOT NULL DEFAULT 'bank' CHECK (method IN ('bank', 'cash', 'offset', 'other')),
        reference     TEXT CHECK (reference IS NULL OR length(reference) <= 500),
        source        TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'qonto', 'import')),
        external_id   TEXT,
        ledger_record_id UUID,
        created_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        reversed_at   TIMESTAMPTZ,
        reversed_by   TEXT,
        reversal_reason TEXT,
        UNIQUE (tenant_id, source, external_id),
        CHECK ((reversed_at IS NULL) = (reversed_by IS NULL))
    )
    """,
    "CREATE INDEX payments_invoice_idx ON payments (invoice_id) WHERE reversed_at IS NULL",
    """
    CREATE FUNCTION guard_payment() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Zahlungen werden nicht gelöscht, sondern storniert' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.reversed_at IS NOT NULL
           OR (to_jsonb(NEW) - ARRAY['reversed_at','reversed_by','reversal_reason','ledger_record_id'])
              IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['reversed_at','reversed_by','reversal_reason','ledger_record_id']) THEN
            RAISE EXCEPTION 'Zahlung ist unveränderlich (nur Storno möglich)' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER payments_guard BEFORE UPDATE OR DELETE ON payments FOR EACH ROW EXECUTE FUNCTION guard_payment()",
    """
    CREATE TABLE base_rates (
        valid_from DATE PRIMARY KEY CHECK (extract(day FROM valid_from) = 1 AND extract(month FROM valid_from) IN (1, 7)),
        rate       NUMERIC(6,2) NOT NULL CHECK (rate BETWEEN -10 AND 20),
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Basiszinssatz laut Deutscher Bundesbank (§ 247 BGB)
    """
    INSERT INTO base_rates (valid_from, rate) VALUES
        ('2023-01-01', 1.62), ('2023-07-01', 3.12), ('2024-01-01', 3.62), ('2024-07-01', 3.37),
        ('2025-01-01', 2.27), ('2025-07-01', 1.27), ('2026-01-01', 1.27)
    """,
    """
    CREATE TABLE dunning_settings (
        tenant_id        UUID PRIMARY KEY,
        enabled          BOOLEAN NOT NULL DEFAULT true,
        auto_send        BOOLEAN NOT NULL DEFAULT true,
        b2c_fee          NUMERIC(10,2) NOT NULL DEFAULT 2.50 CHECK (b2c_fee BETWEEN 0 AND 10),
        b2b_flat_fee     BOOLEAN NOT NULL DEFAULT true,
        deadline_days    INT NOT NULL DEFAULT 7 CHECK (deadline_days BETWEEN 5 AND 30),
        updated_by       TEXT,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE SEQUENCE dunning_number_seq START 1",
    """
    CREATE TABLE dunning_notices (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        number          TEXT NOT NULL DEFAULT ('MA-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('dunning_number_seq')::text, 4, '0')),
        invoice_id      UUID NOT NULL REFERENCES invoices (id),
        level           INT NOT NULL CHECK (level BETWEEN 1 AND 3),
        notice_date     DATE NOT NULL,
        deadline        DATE NOT NULL,
        default_from    DATE,
        principal_open  NUMERIC(19,2) NOT NULL,
        interest        NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (interest >= 0),
        fee             NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (fee >= 0),
        total           NUMERIC(19,2) NOT NULL,
        snapshot        JSONB NOT NULL,
        pdf             BYTEA,
        pdf_sha256      TEXT,
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        sent_at         TIMESTAMPTZ,
        sent_to         TEXT,
        UNIQUE (tenant_id, number),
        UNIQUE (invoice_id, level)
    )
    """,
    """
    CREATE FUNCTION guard_dunning_notice() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Mahnschreiben dürfen nicht gelöscht werden' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['sent_at','sent_to','pdf','pdf_sha256']) IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['sent_at','sent_to','pdf','pdf_sha256'])
           OR (OLD.pdf IS NOT NULL AND NEW.pdf IS DISTINCT FROM OLD.pdf) THEN
            RAISE EXCEPTION 'Mahnschreiben % ist unveränderlich', OLD.number USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER dunning_notices_guard BEFORE UPDATE OR DELETE ON dunning_notices FOR EACH ROW EXECUTE FUNCTION guard_dunning_notice()",
    """
    CREATE TABLE dunning_blocks (
        invoice_id  UUID PRIMARY KEY REFERENCES invoices (id),
        tenant_id   UUID NOT NULL,
        reason      TEXT NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
        until       DATE,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "REVOKE TRUNCATE ON payments, dunning_notices FROM selbstlite_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
