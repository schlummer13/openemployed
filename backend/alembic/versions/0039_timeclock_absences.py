"""Stempeluhr (Check-in/out mit automatischem Pausenabzug) und Abwesenheiten (Urlaub mit Genehmigung, Krankheit …).

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "ALTER TABLE employees ADD COLUMN work_days SMALLINT[] NOT NULL DEFAULT '{0,1,2,3,4}', "
    "ADD COLUMN vacation_carryover NUMERIC(5,1) NOT NULL DEFAULT 0",
    """
    CREATE TABLE time_clock (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      UUID NOT NULL,
        employee_id    UUID NOT NULL REFERENCES employees (id),
        started_at     TIMESTAMPTZ NOT NULL,
        ended_at       TIMESTAMPTZ,
        break_minutes  INTEGER NOT NULL DEFAULT 0 CHECK (break_minutes >= 0),
        worked_minutes INTEGER CHECK (worked_minutes IS NULL OR worked_minutes >= 0),
        source         TEXT NOT NULL DEFAULT 'app' CHECK (source IN ('app', 'portal', 'manual')),
        note           TEXT,
        corrected_by   TEXT,
        correction_reason TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (ended_at IS NULL OR ended_at > started_at)
    )
    """,
    "CREATE UNIQUE INDEX time_clock_open_idx ON time_clock (employee_id) WHERE ended_at IS NULL",
    "CREATE INDEX time_clock_emp_idx ON time_clock (employee_id, started_at DESC)",
    """
    CREATE TABLE absences (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    UUID NOT NULL,
        employee_id  UUID NOT NULL REFERENCES employees (id),
        kind         TEXT NOT NULL CHECK (kind IN ('vacation', 'sick', 'special', 'unpaid', 'overtime', 'school')),
        start_date   DATE NOT NULL,
        end_date     DATE NOT NULL,
        half_start   BOOLEAN NOT NULL DEFAULT false,
        half_end     BOOLEAN NOT NULL DEFAULT false,
        days         NUMERIC(5,1) NOT NULL CHECK (days >= 0),
        status       TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'approved', 'rejected', 'cancelled')),
        note         TEXT,
        decision_note TEXT,
        requested_by TEXT NOT NULL,
        decided_by   TEXT,
        decided_at   TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (end_date >= start_date)
    )
    """,
    "CREATE INDEX absences_emp_idx ON absences (employee_id, start_date)",
    "CREATE INDEX absences_period_idx ON absences (tenant_id, start_date, end_date)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
