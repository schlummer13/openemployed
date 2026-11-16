"""Lieferanten und Belege (Ausgaben) – Phase 1c.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14

Gebuchte Belege sind wie Rechnungen unveränderlich (Ledger + Trigger). Korrektur nur per Stornobuchung
(``reversed_by`` einmalig setzbar).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE suppliers (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        UUID NOT NULL,
        name             TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        country_code     CHAR(2) NOT NULL DEFAULT 'DE' CHECK (country_code ~ '^[A-Z]{2}$'),
        vat_id           TEXT CHECK (vat_id IS NULL OR vat_id ~ '^[A-Z]{2}[A-Za-z0-9+*.]{2,12}$'),
        default_tax_case TEXT NOT NULL DEFAULT 'IN_DE_19',
        default_category TEXT NOT NULL DEFAULT 'other',
        notes            TEXT,
        archived_at      TIMESTAMPTZ,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, name)
    )
    """,
    """
    CREATE TABLE receipt_files (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        content     BYTEA NOT NULL CHECK (length(content) BETWEEN 1 AND 10485760),
        mime_type   TEXT NOT NULL CHECK (mime_type IN ('application/pdf', 'image/png', 'image/jpeg', 'application/xml')),
        file_name   TEXT NOT NULL CHECK (length(file_name) BETWEEN 1 AND 200),
        sha256      TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        uploaded_by TEXT NOT NULL,
        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, sha256)
    )
    """,
    # Originale bleiben unverändert (GoBD) – auch für unverbuchte Belege
    """
    CREATE TRIGGER receipt_files_immutable BEFORE UPDATE OR DELETE ON receipt_files
        FOR EACH ROW EXECUTE FUNCTION forbid_modification()
    """,
    """
    CREATE TABLE receipts (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id         UUID NOT NULL,
        status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'booked')),
        kind              TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('expense', 'reversal')),
        supplier_id       UUID NOT NULL REFERENCES suppliers (id),
        file_id           UUID REFERENCES receipt_files (id),
        receipt_date      DATE NOT NULL,
        service_date      DATE,
        invoice_number    TEXT CHECK (invoice_number IS NULL OR length(invoice_number) <= 100),
        description       TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 500),
        category          TEXT NOT NULL,
        tax_case          TEXT NOT NULL,
        net_amount        NUMERIC(19,2) NOT NULL,
        tax_amount        NUMERIC(19,2) NOT NULL DEFAULT 0,
        gross_amount      NUMERIC(19,2) NOT NULL,
        rc_tax_amount     NUMERIC(19,2) NOT NULL DEFAULT 0,
        input_tax_amount  NUMERIC(19,2) NOT NULL DEFAULT 0,
        expense_amount    NUMERIC(19,2) NOT NULL,
        deductible_amount NUMERIC(19,2) NOT NULL,
        paid_on           DATE,
        reverses_id       UUID REFERENCES receipts (id),
        reversed_by       UUID REFERENCES receipts (id),
        reversal_reason   TEXT,
        ledger_record_id  UUID,
        ledger_hash       TEXT,
        booked_at         TIMESTAMPTZ,
        booked_by         TEXT,
        created_by        TEXT NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (net_amount + tax_amount = gross_amount),
        CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
        CHECK (status = 'draft' OR (ledger_record_id IS NOT NULL AND ledger_hash IS NOT NULL AND booked_at IS NOT NULL
               AND file_id IS NOT NULL)),
        CHECK (reversed_by IS NULL OR status = 'booked')
    )
    """,
    "CREATE INDEX receipts_tenant_idx ON receipts (tenant_id, status, receipt_date)",
    "CREATE UNIQUE INDEX receipts_one_reversal_idx ON receipts (reverses_id) WHERE reverses_id IS NOT NULL",
    """
    CREATE FUNCTION guard_booked_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'booked' THEN
                RAISE EXCEPTION 'Gebuchter Beleg darf nicht gelöscht werden' USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status = 'booked' THEN
            IF (to_jsonb(NEW) - 'reversed_by' - 'paid_on' - 'updated_at')
               IS DISTINCT FROM (to_jsonb(OLD) - 'reversed_by' - 'paid_on' - 'updated_at') THEN
                RAISE EXCEPTION 'Gebuchter Beleg ist unveränderlich – nur Storno möglich' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.reversed_by IS NOT NULL AND NEW.reversed_by IS DISTINCT FROM OLD.reversed_by THEN
                RAISE EXCEPTION 'Beleg ist bereits storniert' USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER receipts_guard BEFORE UPDATE OR DELETE ON receipts
        FOR EACH ROW EXECUTE FUNCTION guard_booked_receipt()
    """,
    "REVOKE TRUNCATE ON suppliers, receipts, receipt_files FROM openemployed_app",
    "REVOKE DELETE ON suppliers FROM openemployed_app",
    "REVOKE UPDATE, DELETE ON receipt_files FROM openemployed_app",
    "REVOKE ALL ON FUNCTION guard_booked_receipt() FROM PUBLIC",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar (Aufbewahrungspflicht)")
