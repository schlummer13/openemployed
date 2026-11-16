"""Betriebliche freie Tage (z. B. Heiligabend, Silvester, Rosenmontag, Betriebsurlaub) zusätzlich zu den gesetzlichen Feiertagen.

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE company_holidays (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
        rule        TEXT NOT NULL DEFAULT 'fixed' CHECK (rule IN ('fixed', 'easter')),
        month       SMALLINT CHECK (month BETWEEN 1 AND 12),
        day         SMALLINT CHECK (day BETWEEN 1 AND 31),
        easter_offset SMALLINT CHECK (easter_offset BETWEEN -80 AND 80),
        year        INTEGER CHECK (year IS NULL OR year BETWEEN 2000 AND 2200),
        half_day    BOOLEAN NOT NULL DEFAULT false,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK ((rule = 'fixed' AND month IS NOT NULL AND day IS NOT NULL) OR (rule = 'easter' AND easter_offset IS NOT NULL))
    )
    """,
    "CREATE INDEX company_holidays_tenant_idx ON company_holidays (tenant_id)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE company_holidays")
