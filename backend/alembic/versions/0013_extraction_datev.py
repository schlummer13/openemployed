"""Belegerkennung (KI/E-Rechnung), DATEV-Export, Bankimport (Phase 3).

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    # Erkennungsergebnisse je Datei (Hash-Cache: derselbe Beleg wird nie zweimal an die KI geschickt)
    """
    CREATE TABLE receipt_extractions (
        file_id     UUID PRIMARY KEY REFERENCES receipt_files (id),
        tenant_id   UUID NOT NULL,
        source      TEXT NOT NULL CHECK (source IN ('einvoice', 'ai')),
        model       TEXT,
        result      JSONB NOT NULL,
        input_tokens  INT,
        output_tokens INT,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # DATEV: Einstellungen, Kontenzuordnung (vom Steuerberater zu bestätigen), Exporthistorie
    """
    CREATE TABLE datev_settings (
        tenant_id        UUID PRIMARY KEY,
        consultant_number INT CHECK (consultant_number IS NULL OR consultant_number BETWEEN 1001 AND 9999999),
        client_number    INT CHECK (client_number IS NULL OR client_number BETWEEN 1 AND 99999),
        chart            TEXT NOT NULL DEFAULT 'SKR04' CHECK (chart IN ('SKR03', 'SKR04')),
        account_length   INT NOT NULL DEFAULT 4 CHECK (account_length BETWEEN 4 AND 8),
        fiscal_year_start INT NOT NULL DEFAULT 1 CHECK (fiscal_year_start BETWEEN 1 AND 12),
        bank_account     TEXT NOT NULL DEFAULT '1800',
        receivables_account TEXT NOT NULL DEFAULT '1200',
        payables_account TEXT NOT NULL DEFAULT '3300',
        use_personal_accounts BOOLEAN NOT NULL DEFAULT true,
        confirmed_by_advisor BOOLEAN NOT NULL DEFAULT false,
        updated_by       TEXT,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE datev_tax_keys (
        tenant_id  UUID NOT NULL,
        tax_case   TEXT NOT NULL,
        account    TEXT,
        bu_key     TEXT CHECK (bu_key IS NULL OR bu_key ~ '^[0-9]{1,4}$'),
        updated_by TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, tax_case)
    )
    """,
    """
    CREATE TABLE datev_exports (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        period_from  DATE NOT NULL,
        period_to    DATE NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('final', 'correction', 'preview')),
        bookings     INT NOT NULL,
        archive      BYTEA NOT NULL,
        sha256       TEXT NOT NULL,
        ledger_record_id UUID,
        created_by   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (period_to >= period_from)
    )
    """,
    # Welche Buchung (Rechnung, Beleg, Zahlung) in welchem Export enthalten ist → Korrekturstapel statt Doppelexport
    """
    CREATE TABLE datev_export_items (
        export_id   UUID NOT NULL REFERENCES datev_exports (id),
        item_type   TEXT NOT NULL CHECK (item_type IN ('invoice', 'receipt', 'payment', 'payment_reversal', 'receipt_payment')),
        item_id     UUID NOT NULL,
        PRIMARY KEY (item_type, item_id, export_id)
    )
    """,
    "CREATE UNIQUE INDEX datev_export_items_once ON datev_export_items (item_type, item_id)",
    """
    CREATE FUNCTION forbid_change_datev() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'DATEV-Exporte sind unveränderlich' USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    "CREATE TRIGGER datev_exports_immutable BEFORE UPDATE OR DELETE ON datev_exports FOR EACH ROW EXECUTE FUNCTION forbid_change_datev()",
    "CREATE TRIGGER datev_export_items_immutable BEFORE UPDATE OR DELETE ON datev_export_items FOR EACH ROW EXECUTE FUNCTION forbid_change_datev()",
    # Bankumsätze (Qonto-API oder CSV-Import) für den Zahlungsabgleich
    """
    CREATE TABLE bank_transactions (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        source        TEXT NOT NULL CHECK (source IN ('qonto', 'csv')),
        external_id   TEXT NOT NULL,
        booked_on     DATE NOT NULL,
        amount        NUMERIC(19,2) NOT NULL,
        currency      CHAR(3) NOT NULL DEFAULT 'EUR',
        counterparty  TEXT,
        counterparty_iban TEXT,
        reference     TEXT,
        status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'matched', 'ignored')),
        matched_invoice_id UUID REFERENCES invoices (id),
        matched_receipt_id UUID REFERENCES receipts (id),
        payment_id    UUID REFERENCES payments (id),
        note          TEXT,
        imported_by   TEXT NOT NULL,
        imported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, source, external_id)
    )
    """,
    "CREATE INDEX bank_transactions_open_idx ON bank_transactions (tenant_id, status, booked_on)",
    # Kreditorennummern (DATEV-Personenkonten) werden beim ersten Export vergeben und nie geändert
    "ALTER TABLE suppliers ADD COLUMN datev_account INT CHECK (datev_account IS NULL OR datev_account BETWEEN 70000 AND 99999)",
    "CREATE UNIQUE INDEX suppliers_datev_account_idx ON suppliers (tenant_id, datev_account) WHERE datev_account IS NOT NULL",
    "REVOKE TRUNCATE ON datev_exports, datev_export_items, bank_transactions FROM selbstlite_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
