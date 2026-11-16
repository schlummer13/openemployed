"""Anlagenbuchhaltung, Abschlussbuchungen (Abschlussperiode JJJJ-13) und zusätzliche Konten.

Revision ID: 0024
Revises: 0023

- journal_entries.closing: Abschlussbuchung zum 31.12. in der Abschlussperiode „JJJJ-13“ (unabhängig von der
  Umsatzsteuer-Sperre des Dezembers, festgeschrieben mit dem Jahresabschluss).
- fixed_assets / asset_depreciations: Anlagenverzeichnis und jährliche Abschreibung (linear monatsgenau, digitale
  Wirtschaftsgüter nach BMF 22.02.2022, Sammelposten § 6 Abs. 2a EStG, GWG-Verzeichnis § 6 Abs. 2 EStG).
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# (SKR03, SKR04, Name, Typ) – Zuordnung mit dem Steuerberater abstimmen
ACCOUNTS = [
    ("4822", "6200", "Abschreibungen auf immaterielle Vermögensgegenstände", "expense"),
    ("4862", "6264", "Abschreibungen auf den Sammelposten Wirtschaftsgüter", "expense"),
    ("2310", "6895", "Anlagenabgänge Sachanlagen (Restbuchwert bei Buchverlust)", "expense"),
    ("4320", "7610", "Gewerbesteuer", "expense"),
    ("2200", "7600", "Körperschaftsteuer", "expense"),
    ("2208", "7608", "Solidaritätszuschlag", "expense"),
    ("0956", "3035", "Gewerbesteuerrückstellung", "liability"),
    ("0955", "3040", "Steuerrückstellungen (Körperschaftsteuer, Soli)", "liability"),
    ("0977", "3095", "Rückstellungen für Abschluss- und Prüfungskosten", "liability"),
    ("0970", "3070", "Sonstige Rückstellungen", "liability"),
    ("0980", "1900", "Aktive Rechnungsabgrenzung", "asset"),
    ("0990", "3900", "Passive Rechnungsabgrenzung", "liability"),
    ("1700", "3500", "Sonstige Verbindlichkeiten", "liability"),
    ("4660", "6650", "Reisekosten Arbeitnehmer (Übernachtung, Nebenkosten)", "expense"),
    ("4663", "6663", "Reisekosten Arbeitnehmer Fahrtkosten", "expense"),
    ("4664", "6664", "Reisekosten Arbeitnehmer Verpflegungsmehraufwand", "expense"),
    ("4668", "6668", "Kilometergelderstattung Arbeitnehmer", "expense"),
]

STATEMENTS: list[str] = [
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    + ", ".join(f"('SKR03', '{a}', '{n}', '{t}'), ('SKR04', '{b}', '{n}', '{t}')" for a, b, n, t in ACCOUNTS)
    + " ON CONFLICT (chart, number) DO NOTHING",
    "ALTER TABLE journal_entries ADD COLUMN closing BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE journal_entries DROP CONSTRAINT journal_entries_source_type_check",
    "ALTER TABLE journal_entries ADD CONSTRAINT journal_entries_source_type_check CHECK (source_type IN ('invoice', 'payment', "
    "'payment_reversal', 'receipt', 'receipt_payment', 'manual', 'depreciation', 'closing', 'travel', 'travel_payment'))",
    """
    CREATE OR REPLACE FUNCTION guard_journal_period() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE locked BOOLEAN := false; per TEXT;
    BEGIN
        -- Abschlussbuchungen zum 31.12. gehören in die Abschlussperiode JJJJ-13
        per := CASE WHEN NEW.closing THEN to_char(NEW.entry_date, 'YYYY') || '-13' ELSE to_char(NEW.entry_date, 'YYYY-MM') END;
        IF NEW.closing AND to_char(NEW.entry_date, 'MM-DD') <> '12-31' THEN
            RAISE EXCEPTION 'Abschlussbuchungen tragen das Datum 31.12.' USING ERRCODE = 'check_violation';
        END IF;
        IF to_regclass('ledger.locked_periods') IS NOT NULL THEN
            EXECUTE 'SELECT EXISTS (SELECT 1 FROM ledger.locked_periods WHERE tenant_id = $1 AND period = $2)'
                INTO locked USING NEW.tenant_id, per;
        END IF;
        IF locked THEN
            RAISE EXCEPTION 'Periode % ist festgeschrieben – Buchung nur in eine offene Periode', per
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TABLE fixed_assets (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          UUID NOT NULL,
        number             INTEGER NOT NULL,
        name               TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        account            TEXT NOT NULL CHECK (account ~ '^[0-9]{4}$'),
        asset_class        TEXT NOT NULL CHECK (asset_class IN ('digital', 'furniture', 'vehicle', 'machine', 'intangible', 'other')),
        method             TEXT NOT NULL CHECK (method IN ('linear', 'digital_full', 'pool', 'gwg')),
        acquisition_date   DATE NOT NULL,
        cost               NUMERIC(19,2) NOT NULL CHECK (cost > 0),
        useful_life_months INTEGER NOT NULL CHECK (useful_life_months BETWEEN 1 AND 600),
        receipt_id         UUID UNIQUE REFERENCES receipts (id),
        disposal_date      DATE,
        disposal_note      TEXT,
        notes              TEXT,
        created_by         TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number),
        CHECK (disposal_date IS NULL OR disposal_date >= acquisition_date)
    )
    """,
    """
    CREATE TABLE asset_depreciations (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        asset_id         UUID NOT NULL REFERENCES fixed_assets (id),
        tenant_id        UUID NOT NULL,
        year             INTEGER NOT NULL,
        amount           NUMERIC(19,2) NOT NULL CHECK (amount >= 0),
        disposal_amount  NUMERIC(19,2) NOT NULL DEFAULT 0,
        journal_entry_id UUID REFERENCES journal_entries (id),
        created_by       TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (asset_id, year)
    )
    """,
    # Gebuchte Abschreibungen sind unveränderlich (Korrektur nur über Gegenbuchung)
    """
    CREATE FUNCTION guard_asset_depreciation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Gebuchte Abschreibungen sind unveränderlich' USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    "CREATE TRIGGER asset_depreciations_immutable BEFORE UPDATE OR DELETE ON asset_depreciations "
    "FOR EACH ROW EXECUTE FUNCTION guard_asset_depreciation()",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
