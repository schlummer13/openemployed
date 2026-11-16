"""Auslandskunden, Fremdwährungen, XRechnung/Peppol.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE customers DROP CONSTRAINT customers_country_code_check",
    "ALTER TABLE customers ADD CONSTRAINT customers_country_code_check CHECK (country_code ~ '^[A-Z]{2}$')",
    "ALTER TABLE customers DROP CONSTRAINT customers_postal_code_check",
    # Deutsche PLZ fünfstellig; ausländische frei (max. 12 Zeichen)
    "ALTER TABLE customers ADD CONSTRAINT customers_postal_code_check CHECK ((country_code = 'DE' AND postal_code ~ '^[0-9]{5}$') "
    "OR (country_code <> 'DE' AND length(postal_code) BETWEEN 1 AND 12))",
    "ALTER TABLE customers DROP CONSTRAINT customers_vat_id_check",
    "ALTER TABLE customers ADD CONSTRAINT customers_vat_id_check CHECK (vat_id IS NULL OR vat_id ~ '^[A-Z]{2}[A-Z0-9]{2,13}$')",
    "ALTER TABLE customers ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$')",
    "ALTER TABLE customers ADD COLUMN einvoice_format TEXT NOT NULL DEFAULT 'facturx' CHECK (einvoice_format IN ('facturx', 'xrechnung'))",
    "ALTER TABLE customers ADD COLUMN peppol_id TEXT CHECK (peppol_id IS NULL OR peppol_id ~ '^[0-9]{4}:[A-Za-z0-9._:-]{1,50}$')",
    "ALTER TABLE invoice_documents ADD COLUMN xrechnung BYTEA, ADD COLUMN xrechnung_sha256 TEXT",
    "ALTER TABLE invoice_deliveries DROP CONSTRAINT invoice_deliveries_channel_check",
    "ALTER TABLE invoice_deliveries ADD CONSTRAINT invoice_deliveries_channel_check CHECK (channel IN ('email', 'peppol'))",
    # Fremdwährung: Rechnung in Währung des Kunden, Buchung in EUR zum Kurs des Rechnungsdatums (EZB-Referenzkurs)
    "ALTER TABLE invoices ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'), "
    "ADD COLUMN exchange_rate NUMERIC(18,8) CHECK (exchange_rate IS NULL OR exchange_rate > 0)",
    "ALTER TABLE payments ADD COLUMN amount_eur NUMERIC(19,2) CHECK (amount_eur IS NULL OR amount_eur > 0)",
    """
    CREATE TABLE exchange_rates (
        currency   CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
        rate_date  DATE NOT NULL,
        rate       NUMERIC(18,8) NOT NULL CHECK (rate > 0),
        source     TEXT NOT NULL DEFAULT 'EZB',
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (currency, rate_date)
    )
    """,
    "GRANT SELECT, INSERT, UPDATE ON exchange_rates TO openemployed_app",
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    "('SKR03', '2668', 'Kursdifferenzen (Erträge)', 'revenue'), ('SKR04', '4840', 'Kursdifferenzen (Erträge)', 'revenue'), "
    "('SKR03', '2150', 'Kursdifferenzen (Aufwand)', 'expense'), ('SKR04', '6880', 'Kursdifferenzen (Aufwand)', 'expense'), "
    "('SKR03', '8338', 'Erlöse sonstige Leistungen EU (§ 13b)', 'revenue'), ('SKR04', '4336', 'Erlöse sonstige Leistungen EU (§ 13b)', 'revenue'), "
    "('SKR03', '8125', 'Steuerfreie innergemeinschaftliche Lieferungen', 'revenue'), "
    "('SKR04', '4125', 'Steuerfreie innergemeinschaftliche Lieferungen', 'revenue'), "
    "('SKR03', '8338', 'Erlöse nicht steuerbar (Drittland)', 'revenue') ON CONFLICT (chart, number) DO NOTHING",
    "INSERT INTO accounts (chart, number, name, type) VALUES ('SKR03', '8339', 'Erlöse nicht steuerbare Leistungen Drittland', 'revenue'), "
    "('SKR04', '4338', 'Erlöse nicht steuerbare Leistungen Drittland', 'revenue') ON CONFLICT (chart, number) DO NOTHING",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
