"""Doppelte Buchführung: Kontenrahmen (SKR03/SKR04, Auszug) und Buchungsjournal.

Revision ID: 0017
Revises: 0016

Buchungssätze sind nach dem Schreiben unveränderlich (GoBD); Korrekturen erfolgen per Stornobuchung. Ein
verzögert geprüfter Constraint-Trigger stellt sicher, dass jeder Buchungssatz ausgeglichen ist (Soll = Haben).
⚠️ Kontenauszug als Vorschlag – mit dem Steuerberater abstimmen.
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# (Konto SKR03, Konto SKR04, Bezeichnung, Typ)
ACCOUNTS: list[tuple[str, str, str, str]] = [
    ("0027", "0135", "EDV-Software", "asset"),
    ("0420", "0650", "Büroeinrichtung", "asset"),
    ("0480", "0670", "Geringwertige Wirtschaftsgüter", "asset"),
    ("0485", "0675", "Wirtschaftsgüter (Sammelposten)", "asset"),
    ("0490", "0690", "Sonstige Betriebs- und Geschäftsausstattung", "asset"),
    ("1400", "1200", "Forderungen aus Lieferungen und Leistungen", "asset"),
    ("1200", "1800", "Bank", "asset"),
    ("1000", "1600", "Kasse", "asset"),
    ("1576", "1406", "Abziehbare Vorsteuer 19 %", "asset"),
    ("1571", "1401", "Abziehbare Vorsteuer 7 %", "asset"),
    ("1577", "1407", "Abziehbare Vorsteuer nach § 13b UStG 19 %", "asset"),
    ("1600", "3300", "Verbindlichkeiten aus Lieferungen und Leistungen", "liability"),
    ("1776", "3806", "Umsatzsteuer 19 %", "liability"),
    ("1771", "3801", "Umsatzsteuer 7 %", "liability"),
    ("1787", "3837", "Umsatzsteuer nach § 13b UStG 19 %", "liability"),
    ("1780", "3820", "Umsatzsteuer-Vorauszahlungen", "liability"),
    ("0800", "2900", "Gezeichnetes Kapital", "equity"),
    ("8400", "4400", "Erlöse 19 % USt", "revenue"),
    ("8300", "4300", "Erlöse 7 % USt", "revenue"),
    ("2650", "7100", "Sonstige Zinsen und ähnliche Erträge", "revenue"),
    ("3100", "5900", "Fremdleistungen", "expense"),
    ("4127", "6024", "Geschäftsführergehälter der GmbH-Gesellschafter", "expense"),
    ("4130", "6110", "Gesetzliche soziale Aufwendungen", "expense"),
    ("4360", "6400", "Versicherungen", "expense"),
    ("4380", "6420", "Beiträge", "expense"),
    ("4600", "6600", "Werbekosten", "expense"),
    ("4630", "6610", "Geschenke abzugsfähig", "expense"),
    ("4650", "6640", "Bewirtungskosten", "expense"),
    ("4654", "6644", "Nicht abzugsfähige Bewirtungskosten", "expense"),
    ("4670", "6670", "Reisekosten Unternehmer", "expense"),
    ("4806", "6495", "Wartungskosten für Hard- und Software", "expense"),
    ("4855", "6260", "Sofortabschreibung geringwertiger Wirtschaftsgüter", "expense"),
    ("4900", "6300", "Sonstige betriebliche Aufwendungen", "expense"),
    ("4920", "6805", "Telefon", "expense"),
    ("4925", "6810", "Telefax und Internetkosten", "expense"),
    ("4930", "6815", "Bürobedarf", "expense"),
    ("4940", "6820", "Zeitschriften, Bücher", "expense"),
    ("4945", "6821", "Fortbildungskosten", "expense"),
    ("4950", "6825", "Rechts- und Beratungskosten", "expense"),
    ("4955", "6830", "Buchführungskosten", "expense"),
    ("4957", "6827", "Abschluss- und Prüfungskosten", "expense"),
    ("4964", "6837", "Aufwendungen für die zeitlich befristete Überlassung von Rechten (Lizenzen)", "expense"),
    ("4970", "6855", "Nebenkosten des Geldverkehrs", "expense"),
    ("4830", "6220", "Abschreibungen auf Sachanlagen", "expense"),
    ("2100", "7300", "Zinsen und ähnliche Aufwendungen", "expense"),
    ("4210", "6310", "Miete", "expense"),
]

STATEMENTS: list[str] = [
    """
    CREATE TABLE accounts (
        chart   TEXT NOT NULL CHECK (chart IN ('SKR03', 'SKR04')),
        number  TEXT NOT NULL CHECK (number ~ '^[0-9]{4}$'),
        name    TEXT NOT NULL,
        type    TEXT NOT NULL CHECK (type IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
        PRIMARY KEY (chart, number)
    )
    """,
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    + ", ".join(f"('SKR03', '{a}', '{n}', '{t}'), ('SKR04', '{b}', '{n}', '{t}')" for a, b, n, t in ACCOUNTS),
    "CREATE SEQUENCE journal_number_seq START 1",
    """
    CREATE TABLE journal_entries (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        number        TEXT NOT NULL DEFAULT ('BU-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('journal_number_seq')::text, 6, '0')),
        chart         TEXT NOT NULL CHECK (chart IN ('SKR03', 'SKR04')),
        entry_date    DATE NOT NULL,
        description   TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
        document_ref  TEXT,
        source_type   TEXT NOT NULL CHECK (source_type IN ('invoice', 'payment', 'payment_reversal', 'receipt', 'receipt_payment', 'manual')),
        source_id     UUID,
        method        TEXT NOT NULL CHECK (method IN ('rule', 'claude', 'manual')),
        ai_model      TEXT,
        ai_reasoning  TEXT,
        ledger_record_id UUID,
        created_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number)
    )
    """,
    "CREATE UNIQUE INDEX journal_entries_source_once ON journal_entries (source_type, source_id) WHERE source_id IS NOT NULL",
    "CREATE INDEX journal_entries_date_idx ON journal_entries (tenant_id, entry_date)",
    """
    CREATE TABLE journal_lines (
        id        BIGSERIAL PRIMARY KEY,
        entry_id  UUID NOT NULL REFERENCES journal_entries (id),
        position  INT NOT NULL,
        account   TEXT NOT NULL CHECK (account ~ '^[0-9]{4}$'),
        debit     NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
        credit    NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
        note      TEXT,
        CHECK ((debit > 0) <> (credit > 0)),
        UNIQUE (entry_id, position)
    )
    """,
    """
    CREATE FUNCTION check_journal_balance() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE diff NUMERIC;
    BEGIN
        SELECT COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0) INTO diff FROM journal_lines WHERE entry_id = NEW.entry_id;
        IF diff <> 0 THEN
            RAISE EXCEPTION 'Buchungssatz nicht ausgeglichen (Differenz %)', diff USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END;
    $$
    """,
    "CREATE CONSTRAINT TRIGGER journal_lines_balanced AFTER INSERT ON journal_lines DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION check_journal_balance()",
    "CREATE TRIGGER journal_entries_immutable BEFORE UPDATE OR DELETE ON journal_entries FOR EACH ROW EXECUTE FUNCTION forbid_modification()",
    "CREATE TRIGGER journal_lines_immutable BEFORE UPDATE OR DELETE ON journal_lines FOR EACH ROW EXECUTE FUNCTION forbid_modification()",
    "REVOKE TRUNCATE ON journal_entries, journal_lines FROM openemployed_app",
    "GRANT SELECT ON accounts TO openemployed_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
