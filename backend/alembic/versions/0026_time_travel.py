"""Zeiterfassung (Abrechnung als Rechnungsentwurf) und Reisekosten (Pauschalen, Erstattung an Geschäftsführer).

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE projects ADD COLUMN hourly_rate NUMERIC(19,2) CHECK (hourly_rate IS NULL OR hourly_rate >= 0)",
    """
    CREATE TABLE time_entries (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      UUID NOT NULL,
        project_id     UUID NOT NULL REFERENCES projects (id),
        task_id        UUID REFERENCES tasks (id) ON DELETE SET NULL,
        shareholder_id UUID REFERENCES shareholders (id),
        work_date      DATE NOT NULL,
        minutes        INTEGER NOT NULL CHECK (minutes BETWEEN 1 AND 1440),
        description    TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 500),
        billable       BOOLEAN NOT NULL DEFAULT true,
        invoice_id     UUID REFERENCES invoices (id) ON DELETE SET NULL,
        created_by     TEXT NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX time_entries_project_idx ON time_entries (tenant_id, project_id, work_date)",
    """
    CREATE TABLE travel_expenses (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        number          INTEGER NOT NULL,
        shareholder_id  UUID NOT NULL REFERENCES shareholders (id),
        purpose         TEXT NOT NULL CHECK (length(purpose) BETWEEN 1 AND 300),
        destination     TEXT NOT NULL CHECK (length(destination) BETWEEN 1 AND 200),
        start_date      DATE NOT NULL,
        start_time      TIME NOT NULL,
        end_date        DATE NOT NULL,
        end_time        TIME NOT NULL,
        km              NUMERIC(9,1) NOT NULL DEFAULT 0 CHECK (km >= 0),
        breakfasts      INTEGER NOT NULL DEFAULT 0 CHECK (breakfasts >= 0),
        lunches         INTEGER NOT NULL DEFAULT 0 CHECK (lunches >= 0),
        dinners         INTEGER NOT NULL DEFAULT 0 CHECK (dinners >= 0),
        other_costs     NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (other_costs >= 0),
        other_note      TEXT,
        meals_amount    NUMERIC(19,2) NOT NULL DEFAULT 0,
        km_amount       NUMERIC(19,2) NOT NULL DEFAULT 0,
        total           NUMERIC(19,2) NOT NULL DEFAULT 0,
        status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'booked', 'paid')),
        journal_entry_id UUID REFERENCES journal_entries (id),
        paid_on         DATE,
        payment_entry_id UUID REFERENCES journal_entries (id),
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number),
        CHECK ((end_date, end_time) > (start_date, start_time))
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
