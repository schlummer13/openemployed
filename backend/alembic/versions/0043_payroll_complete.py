"""Lohn vollständig: ELStAM-Merkmale, Dienstwagen, Pfändungen, Elternzeit, SV-Meldungen (DEÜV), Beitragsnachweise, Lohnsteuerbescheinigungen.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

ACCOUNTS = [
    ("4140", "6130", "Freiwillige soziale Aufwendungen, lohnsteuerfrei", "expense"),
    ("1340", "1340", "Forderungen gegen Personal aus Lohn- und Gehaltsabrechnung", "asset"),
    ("8614", "4947", "Verrechnete sonstige Sachbezüge (ohne Umsatzsteuer)", "revenue"),
]

STATEMENTS: list[str] = [
    "INSERT INTO accounts (chart, number, name, type) VALUES "
    + ", ".join(f"('SKR03', '{a}', '{n}', '{t}'), ('SKR04', '{b}', '{n}', '{t}')" for a, b, n, t in ACCOUNTS)
    + " ON CONFLICT (chart, number) DO NOTHING",
    "ALTER TABLE employees "
    "ADD COLUMN tax_factor NUMERIC(4,3) CHECK (tax_factor IS NULL OR (tax_factor > 0 AND tax_factor < 1)), "
    "ADD COLUMN tax_allowance_month NUMERIC(10,2) NOT NULL DEFAULT 0, "
    "ADD COLUMN tax_addition_month NUMERIC(10,2) NOT NULL DEFAULT 0, "
    "ADD COLUMN activity_key TEXT CHECK (activity_key IS NULL OR activity_key ~ '^[0-9]{9}$'), "
    "ADD COLUMN nationality TEXT NOT NULL DEFAULT 'DE', "
    "ADD COLUMN birth_name TEXT, ADD COLUMN birth_place TEXT, "
    "ADD COLUMN car_list_price NUMERIC(12,2), ADD COLUMN car_distance_km NUMERIC(6,1), "
    "ADD COLUMN car_kind TEXT CHECK (car_kind IS NULL OR car_kind IN ('combustion', 'hybrid', 'electric')), "
    "ADD COLUMN car_copayment NUMERIC(10,2) NOT NULL DEFAULT 0",
    "ALTER TABLE absences DROP CONSTRAINT IF EXISTS absences_kind_check",
    "ALTER TABLE absences ADD CONSTRAINT absences_kind_check CHECK (kind IN ('vacation', 'sick', 'special', 'unpaid', 'overtime', "
    "'school', 'parental', 'maternity'))",
    """
    CREATE TABLE payroll_settings (
        tenant_id         UUID PRIMARY KEY,
        employer_number   TEXT CHECK (employer_number IS NULL OR employer_number ~ '^[0-9]{8}$'),
        u1_rate           NUMERIC(5,2),
        u2_rate           NUMERIC(5,2),
        garnishment_base  NUMERIC(10,2),
        garnishment_first NUMERIC(10,2),
        garnishment_more  NUMERIC(10,2),
        garnishment_cap   NUMERIC(10,2),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE garnishments (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        employee_id   UUID NOT NULL REFERENCES employees (id),
        creditor      TEXT NOT NULL CHECK (length(creditor) BETWEEN 1 AND 200),
        reference     TEXT NOT NULL,
        creditor_iban TEXT,
        kind          TEXT NOT NULL DEFAULT 'table' CHECK (kind IN ('table', 'fixed', 'assignment')),
        fixed_amount  NUMERIC(10,2),
        dependents    INTEGER NOT NULL DEFAULT 0 CHECK (dependents BETWEEN 0 AND 10),
        claim_total   NUMERIC(12,2) NOT NULL CHECK (claim_total > 0),
        served_on     DATE NOT NULL,
        active        BOOLEAN NOT NULL DEFAULT true,
        notes         TEXT,
        created_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE sv_reports (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        employee_id   UUID NOT NULL REFERENCES employees (id),
        reason        TEXT NOT NULL,
        period_from   DATE NOT NULL,
        period_to     DATE,
        sv_wage       NUMERIC(12,2),
        data          JSONB NOT NULL DEFAULT '{}',
        due_date      DATE NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'submitted', 'cancelled')),
        submitted_at  TIMESTAMPTZ,
        submitted_by  TEXT,
        reference     TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (employee_id, reason, period_from)
    )
    """,
    """
    CREATE TABLE contribution_statements (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        period        TEXT NOT NULL,
        insurer       TEXT NOT NULL,
        data          JSONB NOT NULL,
        total         NUMERIC(12,2) NOT NULL,
        submit_by     DATE NOT NULL,
        due_date      DATE NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'submitted')),
        submitted_at  TIMESTAMPTZ,
        submitted_by  TEXT,
        UNIQUE (tenant_id, period, insurer)
    )
    """,
    """
    CREATE TABLE wage_tax_certificates (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        employee_id   UUID NOT NULL REFERENCES employees (id),
        year          INTEGER NOT NULL,
        data          JSONB NOT NULL,
        status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'transmitted')),
        transmitted_at TIMESTAMPTZ,
        ticket        TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (employee_id, year)
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
