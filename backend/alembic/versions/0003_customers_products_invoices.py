"""Kunden, Produkte, Rechnungen (Phase 1a).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14

Schutz ausgestellter Rechnungen (GoBD):
- Die verbindliche Fassung liegt festgeschrieben im Ledger (Hash-Kette, eigener DB-Benutzer).
- Zusätzlich verhindert ein Trigger, dass eine ausgestellte Rechnung hier verändert oder gelöscht wird.
  Erlaubt bleibt nur: Verweis auf die Stornorechnung setzen (einmalig) und Versandzeitpunkt.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "CREATE SEQUENCE customer_number_seq START 10001",
    """
    CREATE TABLE customers (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          UUID NOT NULL,
        number             TEXT NOT NULL DEFAULT ('K-' || nextval('customer_number_seq')),
        customer_type      TEXT NOT NULL CHECK (customer_type IN ('b2b', 'b2c')),
        company_name       TEXT CHECK (company_name IS NULL OR length(company_name) BETWEEN 1 AND 200),
        first_name         TEXT CHECK (first_name IS NULL OR length(first_name) <= 100),
        last_name          TEXT CHECK (last_name IS NULL OR length(last_name) <= 100),
        email              TEXT NOT NULL CHECK (email = lower(email) AND email ~ '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'),
        phone              TEXT,
        street             TEXT NOT NULL CHECK (length(street) BETWEEN 1 AND 200),
        postal_code        TEXT NOT NULL CHECK (postal_code ~ '^[0-9]{5}$'),
        city               TEXT NOT NULL CHECK (length(city) BETWEEN 1 AND 100),
        country_code       CHAR(2) NOT NULL DEFAULT 'DE' CHECK (country_code = 'DE'),
        vat_id             TEXT CHECK (vat_id IS NULL OR vat_id ~ '^DE[0-9]{9}$'),
        buyer_reference    TEXT CHECK (buyer_reference IS NULL OR length(buyer_reference) <= 100),
        payment_terms_days INT NOT NULL DEFAULT 14 CHECK (payment_terms_days BETWEEN 0 AND 120),
        notes              TEXT,
        archived_at        TIMESTAMPTZ,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number),
        CHECK (customer_type <> 'b2b' OR company_name IS NOT NULL),
        CHECK (customer_type <> 'b2c' OR last_name IS NOT NULL)
    )
    """,
    "CREATE INDEX customers_tenant_idx ON customers (tenant_id, archived_at)",
    """
    CREATE TABLE products (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        sku         TEXT NOT NULL CHECK (sku ~ '^[A-Za-z0-9._-]{1,40}$'),
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        description TEXT CHECK (description IS NULL OR length(description) <= 2000),
        unit_code   TEXT NOT NULL CHECK (unit_code IN ('C62','HUR','MIN','DAY','WEE','MON','ANN','LS','KMT')),
        price       NUMERIC(19,4) NOT NULL CHECK (price >= 0),
        price_basis TEXT NOT NULL CHECK (price_basis IN ('net', 'gross')),
        tax_case    TEXT NOT NULL CHECK (tax_case IN ('OUT_DE_19', 'OUT_DE_7')),
        billing     TEXT NOT NULL CHECK (billing IN ('one_time', 'monthly', 'yearly', 'hourly')),
        active      BOOLEAN NOT NULL DEFAULT true,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, sku)
    )
    """,
    """
    CREATE TABLE invoices (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          UUID NOT NULL,
        kind               TEXT NOT NULL DEFAULT 'invoice' CHECK (kind IN ('invoice', 'cancellation')),
        status             TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'issued')),
        customer_id        UUID NOT NULL REFERENCES customers (id),
        price_mode         TEXT NOT NULL CHECK (price_mode IN ('net', 'gross')),
        service_date_from  DATE,
        service_date_to    DATE,
        due_days           INT NOT NULL DEFAULT 14 CHECK (due_days BETWEEN 0 AND 120),
        intro_text         TEXT CHECK (intro_text IS NULL OR length(intro_text) <= 2000),
        closing_text       TEXT CHECK (closing_text IS NULL OR length(closing_text) <= 2000),
        cancels_invoice_id UUID REFERENCES invoices (id),
        cancellation_reason TEXT CHECK (cancellation_reason IS NULL OR length(cancellation_reason) <= 500),
        -- ab Freigabe gesetzt
        number             TEXT,
        issue_date         DATE,
        due_date           DATE,
        total_net          NUMERIC(19,2),
        total_tax          NUMERIC(19,2),
        total_gross        NUMERIC(19,2),
        snapshot           JSONB,
        ledger_record_id   UUID,
        ledger_hash        TEXT,
        issued_by          TEXT,
        issued_at          TIMESTAMPTZ,
        -- nach Freigabe noch änderbar
        cancelled_by       UUID REFERENCES invoices (id),
        sent_at            TIMESTAMPTZ,
        created_by         TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number),
        CHECK (service_date_to IS NULL OR service_date_from IS NULL OR service_date_to >= service_date_from),
        CHECK ((kind = 'cancellation') = (cancels_invoice_id IS NOT NULL)),
        CHECK (status = 'draft' OR (number IS NOT NULL AND issue_date IS NOT NULL AND due_date IS NOT NULL
               AND total_gross IS NOT NULL AND snapshot IS NOT NULL AND ledger_record_id IS NOT NULL
               AND ledger_hash IS NOT NULL AND issued_at IS NOT NULL)),
        CHECK (cancelled_by IS NULL OR status = 'issued')
    )
    """,
    "CREATE INDEX invoices_tenant_status_idx ON invoices (tenant_id, status, issue_date)",
    "CREATE INDEX invoices_customer_idx ON invoices (customer_id)",
    "CREATE UNIQUE INDEX invoices_one_cancellation_idx ON invoices (cancels_invoice_id) "
    "WHERE cancels_invoice_id IS NOT NULL",
    """
    CREATE TABLE invoice_lines (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        invoice_id  UUID NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
        position    INT NOT NULL CHECK (position BETWEEN 1 AND 500),
        product_id  UUID REFERENCES products (id),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 1000),
        quantity    NUMERIC(19,4) NOT NULL,
        unit_code   TEXT NOT NULL,
        unit_price  NUMERIC(19,4) NOT NULL CHECK (unit_price >= 0),
        tax_case    TEXT NOT NULL,
        UNIQUE (invoice_id, position)
    )
    """,
    # ── Schutz ausgestellter Rechnungen ──
    """
    CREATE FUNCTION guard_issued_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'issued' THEN
                RAISE EXCEPTION 'Ausgestellte Rechnung % darf nicht gelöscht werden', OLD.number
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status = 'issued' THEN
            IF (to_jsonb(NEW) - 'cancelled_by' - 'sent_at' - 'updated_at')
               IS DISTINCT FROM (to_jsonb(OLD) - 'cancelled_by' - 'sent_at' - 'updated_at') THEN
                RAISE EXCEPTION 'Ausgestellte Rechnung % ist unveränderlich – nur Storno möglich', OLD.number
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.cancelled_by IS NOT NULL AND NEW.cancelled_by IS DISTINCT FROM OLD.cancelled_by THEN
                RAISE EXCEPTION 'Rechnung % ist bereits storniert', OLD.number
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER invoices_guard BEFORE UPDATE OR DELETE ON invoices
        FOR EACH ROW EXECUTE FUNCTION guard_issued_invoice()
    """,
    """
    CREATE FUNCTION guard_issued_invoice_lines() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        parent_status TEXT;
    BEGIN
        SELECT status INTO parent_status FROM invoices
         WHERE id = COALESCE(NEW.invoice_id, OLD.invoice_id);
        IF parent_status = 'issued' THEN
            RAISE EXCEPTION 'Positionen einer ausgestellten Rechnung sind unveränderlich'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN COALESCE(NEW, OLD);
    END;
    $$
    """,
    """
    CREATE TRIGGER invoice_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines
        FOR EACH ROW EXECUTE FUNCTION guard_issued_invoice_lines()
    """,
    "REVOKE TRUNCATE ON customers, products, invoices, invoice_lines FROM selbstlite_app",
    # Kunden und Produkte werden archiviert, nicht gelöscht (Verweise aus Rechnungen)
    "REVOKE DELETE ON customers, products FROM selbstlite_app",
    "REVOKE ALL ON FUNCTION guard_issued_invoice(), guard_issued_invoice_lines() FROM PUBLIC",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar (Aufbewahrungspflicht für Rechnungen)")
