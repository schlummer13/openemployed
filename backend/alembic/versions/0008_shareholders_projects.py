"""Gesellschafter/Geschäftsführergehalt und Projekte/Kanban (Phase 1f).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE shareholders (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id           UUID NOT NULL,
        name                TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        email               TEXT CHECK (email IS NULL OR email = lower(email)),
        share_percent       NUMERIC(7,4) NOT NULL CHECK (share_percent > 0 AND share_percent <= 100),
        is_managing_director BOOLEAN NOT NULL DEFAULT true,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, name)
    )
    """,
    # Vergütung versioniert (Erhöhungen nur für die Zukunft – vGA-Rückwirkungsverbot)
    """
    CREATE TABLE md_compensations (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        shareholder_id    UUID NOT NULL REFERENCES shareholders (id),
        valid_from        DATE NOT NULL,
        gross_month       NUMERIC(12,2) NOT NULL CHECK (gross_month >= 0),
        salaries_per_year INT NOT NULL DEFAULT 12 CHECK (salaries_per_year BETWEEN 12 AND 14),
        sv_status         TEXT NOT NULL DEFAULT 'pflichtig' CHECK (sv_status IN ('pflichtig', 'frei')),
        tax_class         INT NOT NULL DEFAULT 1 CHECK (tax_class BETWEEN 1 AND 6),
        church_tax        BOOLEAN NOT NULL DEFAULT false,
        childless         BOOLEAN NOT NULL DEFAULT true,
        created_by        TEXT NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (shareholder_id, valid_from)
    )
    """,
    """
    CREATE TRIGGER md_compensations_immutable BEFORE UPDATE OR DELETE ON md_compensations
        FOR EACH ROW EXECUTE FUNCTION forbid_modification()
    """,
    """
    CREATE TABLE projects (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        description TEXT,
        customer_id UUID REFERENCES customers (id),
        color       TEXT NOT NULL DEFAULT '#3b6cf6' CHECK (color ~ '^#[0-9a-fA-F]{6}$'),
        archived_at TIMESTAMPTZ,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE project_columns (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
        name       TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
        position   INT NOT NULL,
        is_done    BOOLEAN NOT NULL DEFAULT false,
        UNIQUE (project_id, position)
    )
    """,
    """
    CREATE TABLE tasks (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        project_id  UUID NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
        column_id   UUID NOT NULL REFERENCES project_columns (id),
        title       TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
        description TEXT CHECK (description IS NULL OR length(description) <= 10000),
        assignee    TEXT CHECK (assignee IS NULL OR assignee = lower(assignee)),
        due_date    DATE,
        priority    TEXT NOT NULL DEFAULT 'mittel' CHECK (priority IN ('niedrig', 'mittel', 'hoch', 'dringend')),
        position    DOUBLE PRECISION NOT NULL,
        completed_at TIMESTAMPTZ,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX tasks_board_idx ON tasks (project_id, column_id, position)",
    "REVOKE TRUNCATE ON shareholders, md_compensations, projects, project_columns, tasks FROM selbstlite_app",
    "REVOKE UPDATE, DELETE ON md_compensations FROM selbstlite_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
