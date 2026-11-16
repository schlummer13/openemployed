"""Gesellschafter-Stammdaten, Aufgaben-Zuordnung, Kontostände, Willkommensmail, KI-Anbieter, Finanzeinstellungen.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    # KI-Kontierung unabhängig vom Anbieter (lokal oder Claude)
    "ALTER TABLE journal_entries DROP CONSTRAINT journal_entries_method_check",
    "ALTER TABLE journal_entries ADD CONSTRAINT journal_entries_method_check CHECK (method IN ('rule', 'claude', 'ai', 'manual'))",
    # Gesellschafter vollständig
    "ALTER TABLE shareholders ADD COLUMN phone TEXT CHECK (phone IS NULL OR length(phone) <= 50)",
    "ALTER TABLE shareholders ADD COLUMN birth_date DATE",
    "ALTER TABLE shareholders ADD COLUMN street TEXT CHECK (street IS NULL OR length(street) <= 200)",
    "ALTER TABLE shareholders ADD COLUMN postal_code TEXT CHECK (postal_code IS NULL OR postal_code ~ '^[0-9]{5}$')",
    "ALTER TABLE shareholders ADD COLUMN city TEXT CHECK (city IS NULL OR length(city) <= 100)",
    "ALTER TABLE shareholders ADD COLUMN tax_id TEXT CHECK (tax_id IS NULL OR tax_id ~ '^[0-9]{11}$')",
    "ALTER TABLE shareholders ADD COLUMN social_security_number TEXT CHECK (social_security_number IS NULL OR social_security_number ~ '^[0-9]{2}[0-9]{6}[A-Z][0-9]{3}$')",
    "ALTER TABLE shareholders ADD COLUMN iban TEXT CHECK (iban IS NULL OR iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}$')",
    "ALTER TABLE shareholders ADD COLUMN role_title TEXT CHECK (role_title IS NULL OR length(role_title) <= 100)",
    "ALTER TABLE shareholders ADD COLUMN joined_on DATE",
    "ALTER TABLE shareholders ADD COLUMN color TEXT NOT NULL DEFAULT '#6366f1' CHECK (color ~ '^#[0-9a-fA-F]{6}$')",
    "ALTER TABLE shareholders ADD COLUMN photo BYTEA CHECK (photo IS NULL OR length(photo) <= 2097152)",
    "ALTER TABLE shareholders ADD COLUMN photo_mime TEXT CHECK (photo_mime IS NULL OR photo_mime IN ('image/png', 'image/jpeg', 'image/webp'))",
    # Aufgaben einem Gesellschafter zuordnen
    "ALTER TABLE tasks ADD COLUMN assignee_id UUID REFERENCES shareholders (id)",
    "CREATE INDEX tasks_assignee_idx ON tasks (assignee_id) WHERE completed_at IS NULL",
    # Kontostände (manuell erfasst oder aus Kontoauszug/CAMT/Qonto)
    """
    CREATE TABLE bank_balances (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        balance_on  DATE NOT NULL,
        amount      NUMERIC(19,2) NOT NULL,
        account_iban TEXT,
        source      TEXT NOT NULL CHECK (source IN ('manual', 'camt', 'qonto', 'fints')),
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX bank_balances_idx ON bank_balances (tenant_id, balance_on DESC, created_at DESC)",
    # Finanzeinstellungen für Liquiditätsplanung und Automatisierungen
    """
    CREATE TABLE finance_settings (
        tenant_id          UUID PRIMARY KEY,
        min_liquidity      NUMERIC(19,2) NOT NULL DEFAULT 5000 CHECK (min_liquidity >= 0),
        monthly_fixed_costs NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (monthly_fixed_costs >= 0),
        offer_followup_days INT NOT NULL DEFAULT 7 CHECK (offer_followup_days BETWEEN 2 AND 60),
        offer_auto_reminder BOOLEAN NOT NULL DEFAULT false,
        welcome_mail        BOOLEAN NOT NULL DEFAULT true,
        updated_by         TEXT,
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE customers ADD COLUMN welcome_sent_at TIMESTAMPTZ",
    "ALTER TABLE offers ADD COLUMN reminder_sent_at TIMESTAMPTZ",
    # Erinnerungsvermerk darf nach dem Versand gesetzt werden (Inhalt bleibt unveränderlich)
    """
    CREATE OR REPLACE FUNCTION guard_sent_offer() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'Versendetes Angebot % darf nicht gelöscht werden', OLD.number USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status <> 'draft' AND (to_jsonb(NEW) - ARRAY['status','decided_at','decided_by_name','decision_channel','decision_note',
                                      'invoice_id','contract_id','updated_at','reminder_sent_at'])
           IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','decided_at','decided_by_name','decision_channel','decision_note',
                                      'invoice_id','contract_id','updated_at','reminder_sent_at']) THEN
            RAISE EXCEPTION 'Versendetes Angebot % ist unveränderlich – neue Version anlegen', OLD.number
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
