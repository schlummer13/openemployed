"""Mitarbeiter, Lohnläufe und Gehaltsabrechnungen (Self-Service über das Portal per E-Mail-Code).

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

ACCOUNTS = [  # (SKR03, SKR04, Name, Typ) – mit dem Steuerberater abstimmen
    ("4120", "6020", "Gehälter", "expense"),
    ("4190", "6035", "Löhne für Minijobs", "expense"),
    ("4199", "6040", "Pauschale Steuer für Aushilfen", "expense"),
    ("1740", "3720", "Verbindlichkeiten aus Lohn und Gehalt", "liability"),
    ("1741", "3730", "Verbindlichkeiten aus Lohn- und Kirchensteuer", "liability"),
    ("1742", "3740", "Verbindlichkeiten im Rahmen der sozialen Sicherheit", "liability"),
]

STATEMENTS: list[str] = [
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    + ", ".join(f"('SKR03', '{a}', '{n}', '{t}'), ('SKR04', '{b}', '{n}', '{t}')" for a, b, n, t in ACCOUNTS)
    + " ON CONFLICT (chart, number) DO NOTHING",
    """
    CREATE TABLE employees (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          UUID NOT NULL,
        number             INTEGER NOT NULL,
        first_name         TEXT NOT NULL CHECK (length(first_name) BETWEEN 1 AND 100),
        last_name          TEXT NOT NULL CHECK (length(last_name) BETWEEN 1 AND 100),
        email              TEXT CHECK (email IS NULL OR (email = lower(email) AND position('@' in email) > 1)),
        phone              TEXT,
        birth_date         DATE,
        street             TEXT,
        postal_code        TEXT,
        city               TEXT,
        position           TEXT,
        entry_date         DATE NOT NULL,
        exit_date          DATE,
        employment_type    TEXT NOT NULL CHECK (employment_type IN ('regular', 'minijob', 'werkstudent')),
        salary_type        TEXT NOT NULL CHECK (salary_type IN ('monthly', 'hourly')),
        gross_month        NUMERIC(19,2) CHECK (gross_month IS NULL OR gross_month >= 0),
        hourly_rate        NUMERIC(19,2) CHECK (hourly_rate IS NULL OR hourly_rate >= 0),
        weekly_hours       NUMERIC(5,2),
        salaries_per_year  INTEGER NOT NULL DEFAULT 12 CHECK (salaries_per_year BETWEEN 12 AND 14),
        vacation_days      INTEGER,
        tax_id             TEXT,
        tax_class          INTEGER NOT NULL DEFAULT 1 CHECK (tax_class BETWEEN 1 AND 6),
        child_allowances   NUMERIC(3,1) NOT NULL DEFAULT 0,
        church_tax         BOOLEAN NOT NULL DEFAULT false,
        church_denomination TEXT NOT NULL DEFAULT 'ev' CHECK (church_denomination IN ('ev', 'rk', 'other')),
        state              TEXT NOT NULL DEFAULT 'NW',
        social_security_number TEXT,
        health_insurance   TEXT NOT NULL DEFAULT 'gkv' CHECK (health_insurance IN ('gkv', 'pkv')),
        health_insurer     TEXT,
        kv_additional_rate NUMERIC(5,2),
        pkv_premium        NUMERIC(19,2) NOT NULL DEFAULT 0,
        children_under_25  INTEGER NOT NULL DEFAULT 0,
        rv_exempt          BOOLEAN NOT NULL DEFAULT false,
        iban               TEXT,
        account_holder     TEXT,
        portal_enabled     BOOLEAN NOT NULL DEFAULT false,
        notes              TEXT,
        archived_at        TIMESTAMPTZ,
        created_by         TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number),
        CHECK (exit_date IS NULL OR exit_date >= entry_date),
        CHECK ((salary_type = 'monthly' AND gross_month IS NOT NULL) OR (salary_type = 'hourly' AND hourly_rate IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX employees_email_idx ON employees (tenant_id, email) WHERE email IS NOT NULL AND archived_at IS NULL",
    """
    CREATE TABLE payroll_runs (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id         UUID NOT NULL,
        period            TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
        status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
        journal_entry_id  UUID REFERENCES journal_entries (id),
        salaries_paid_on  DATE,
        taxes_paid_on     DATE,
        sv_paid_on        DATE,
        approved_by       TEXT,
        approved_at       TIMESTAMPTZ,
        created_by        TEXT NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, period)
    )
    """,
    """
    CREATE TABLE payslips (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id           UUID NOT NULL REFERENCES payroll_runs (id) ON DELETE CASCADE,
        tenant_id        UUID NOT NULL,
        employee_id      UUID REFERENCES employees (id),
        shareholder_id   UUID REFERENCES shareholders (id),
        name             TEXT NOT NULL,
        employment_type  TEXT NOT NULL,
        hours            NUMERIC(7,2),
        gross            NUMERIC(19,2) NOT NULL,
        lst              NUMERIC(19,2) NOT NULL,
        solz             NUMERIC(19,2) NOT NULL,
        church           NUMERIC(19,2) NOT NULL,
        rv_ee            NUMERIC(19,2) NOT NULL, av_ee NUMERIC(19,2) NOT NULL, kv_ee NUMERIC(19,2) NOT NULL, pv_ee NUMERIC(19,2) NOT NULL,
        rv_er            NUMERIC(19,2) NOT NULL, av_er NUMERIC(19,2) NOT NULL, kv_er NUMERIC(19,2) NOT NULL, pv_er NUMERIC(19,2) NOT NULL,
        levies           NUMERIC(19,2) NOT NULL,
        flat_tax         NUMERIC(19,2) NOT NULL DEFAULT 0,
        pkv_subsidy      NUMERIC(19,2) NOT NULL DEFAULT 0,
        net              NUMERIC(19,2) NOT NULL,
        payout           NUMERIC(19,2) NOT NULL,
        employer_cost    NUMERIC(19,2) NOT NULL,
        iban             TEXT,
        details          JSONB NOT NULL DEFAULT '{}'::jsonb,
        CHECK ((employee_id IS NULL) <> (shareholder_id IS NULL))
    )
    """,
    "CREATE INDEX payslips_run_idx ON payslips (run_id)",
    # Freigegebene Lohnläufe und ihre Abrechnungen sind unveränderlich
    """
    CREATE FUNCTION guard_payslip() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE st TEXT;
    BEGIN
        SELECT status INTO st FROM payroll_runs WHERE id = COALESCE(OLD.run_id, NEW.run_id);
        IF st = 'approved' THEN
            RAISE EXCEPTION 'Freigegebene Gehaltsabrechnungen sind unveränderlich' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN COALESCE(NEW, OLD);
    END;
    $$
    """,
    "CREATE TRIGGER payslips_guard BEFORE UPDATE OR DELETE ON payslips FOR EACH ROW EXECUTE FUNCTION guard_payslip()",
    "ALTER TABLE elster_submissions ADD COLUMN kind TEXT NOT NULL DEFAULT 'ustva' CHECK (kind IN ('ustva', 'lsta'))",
    "ALTER TABLE journal_entries DROP CONSTRAINT journal_entries_source_type_check",
    "ALTER TABLE journal_entries ADD CONSTRAINT journal_entries_source_type_check CHECK (source_type IN ('invoice', 'payment', "
    "'payment_reversal', 'receipt', 'receipt_payment', 'manual', 'depreciation', 'closing', 'travel', 'travel_payment', 'payroll', "
    "'payroll_payment', 'opening', 'import', 'tax_payment'))",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
