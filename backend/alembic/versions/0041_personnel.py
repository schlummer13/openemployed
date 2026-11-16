"""Personal: Zeitkonto (Überstunden), On-/Offboarding-Checklisten, Personalakte mit Verträgen, Lohnpositionen.

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE employees ADD COLUMN time_account_start DATE, "
    "ADD COLUMN overtime_rule TEXT NOT NULL DEFAULT 'account' CHECK (overtime_rule IN ('account', 'payout', 'included')), "
    "ADD COLUMN overtime_cap_hours NUMERIC(6,2) CHECK (overtime_cap_hours IS NULL OR overtime_cap_hours >= 0)",
    """
    CREATE TABLE time_account_bookings (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        employee_id  UUID NOT NULL REFERENCES employees (id),
        booked_on    DATE NOT NULL,
        minutes      INTEGER NOT NULL CHECK (minutes <> 0),
        kind         TEXT NOT NULL CHECK (kind IN ('opening', 'payout', 'adjustment', 'expiry')),
        reason       TEXT NOT NULL CHECK (length(reason) BETWEEN 3 AND 300),
        payroll_period TEXT CHECK (payroll_period IS NULL OR payroll_period ~ '^[0-9]{4}-[0-9]{2}$'),
        created_by   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX time_account_emp_idx ON time_account_bookings (employee_id, booked_on)",
    """
    CREATE TABLE hr_checklist_templates (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('onboarding', 'offboarding')),
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        items       JSONB NOT NULL DEFAULT '[]',
        is_default  BOOLEAN NOT NULL DEFAULT false,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE hr_checklists (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        employee_id  UUID NOT NULL REFERENCES employees (id),
        kind         TEXT NOT NULL CHECK (kind IN ('onboarding', 'offboarding')),
        name         TEXT NOT NULL,
        reference_date DATE NOT NULL,
        created_by   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE hr_checklist_items (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        checklist_id UUID NOT NULL REFERENCES hr_checklists (id) ON DELETE CASCADE,
        position     INTEGER NOT NULL,
        title        TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        description  TEXT,
        responsible  TEXT,
        due_date     DATE,
        legal        TEXT,
        done_at      TIMESTAMPTZ,
        done_by      TEXT
    )
    """,
    "CREATE INDEX hr_checklist_items_idx ON hr_checklist_items (checklist_id, position)",
    """
    CREATE TABLE hr_contract_templates (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        category    TEXT NOT NULL DEFAULT 'contract',
        body        TEXT NOT NULL,
        created_by  TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE personnel_documents (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     UUID NOT NULL,
        employee_id   UUID NOT NULL REFERENCES employees (id),
        category      TEXT NOT NULL CHECK (category IN ('contract', 'amendment', 'application', 'certificate', 'reference', 'warning',
                                                        'identity', 'training', 'health', 'payroll', 'termination', 'other')),
        title         TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        filename      TEXT NOT NULL,
        mime_type     TEXT NOT NULL,
        size          INTEGER NOT NULL,
        sha256        TEXT NOT NULL,
        content       BYTEA,
        valid_from    DATE,
        valid_until   DATE,
        visible_to_employee BOOLEAN NOT NULL DEFAULT false,
        signature_status TEXT CHECK (signature_status IN ('pending', 'signed', 'rejected')),
        signed_content BYTEA,
        signed_sha256 TEXT,
        delete_after  DATE,
        notes         TEXT,
        created_by    TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at    TIMESTAMPTZ,
        deleted_by    TEXT
    )
    """,
    "CREATE INDEX personnel_documents_emp_idx ON personnel_documents (employee_id, created_at DESC)",
    "ALTER TABLE signature_requests DROP CONSTRAINT IF EXISTS signature_requests_subject_type_check",
    "ALTER TABLE signature_requests ADD CONSTRAINT signature_requests_subject_type_check "
    "CHECK (subject_type IN ('offer', 'contract', 'personnel_document'))",
    """
    CREATE TABLE payroll_items (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        employee_id UUID NOT NULL REFERENCES employees (id),
        period      TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
        kind        TEXT NOT NULL CHECK (kind IN ('overtime', 'bonus', 'one_time', 'benefit', 'company_car', 'tax_free', 'advance',
                                                  'deduction', 'garnishment', 'sick_pay_end', 'parental_leave')),
        label       TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 120),
        quantity    NUMERIC(10,2),
        rate        NUMERIC(12,2),
        amount      NUMERIC(12,2) NOT NULL,
        details     JSONB NOT NULL DEFAULT '{}',
        source      TEXT,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX payroll_items_period_idx ON payroll_items (tenant_id, period, employee_id)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
