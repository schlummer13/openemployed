"""Budgetplanung: Bereiche mit Monatsbudget und Zuordnung gebuchter Ausgaben.

Revision ID: 0021
Revises: 0020

Die Zuordnung ist eine reine Auswertung (Kostenstellen-Sicht) und bleibt änderbar; die festgeschriebenen Belege selbst
werden dabei nicht verändert.
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE budget_areas (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      UUID NOT NULL,
        name           TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        description    TEXT CHECK (description IS NULL OR length(description) <= 500),
        monthly_budget NUMERIC(19,2) NOT NULL CHECK (monthly_budget >= 0),
        color          TEXT NOT NULL DEFAULT '#6366f1' CHECK (color ~ '^#[0-9a-fA-F]{6}$'),
        categories     TEXT[] NOT NULL DEFAULT '{}',
        archived_at    TIMESTAMPTZ,
        created_by     TEXT NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX budget_areas_name_idx ON budget_areas (tenant_id, lower(name)) WHERE archived_at IS NULL",
    """
    CREATE TABLE budget_assignments (
        receipt_id  UUID PRIMARY KEY REFERENCES receipts (id),
        area_id     UUID NOT NULL REFERENCES budget_areas (id),
        tenant_id   UUID NOT NULL,
        assigned_by TEXT NOT NULL,
        assigned_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX budget_assignments_area_idx ON budget_assignments (area_id)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
