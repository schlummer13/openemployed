"""Vertrieb: Leads/Verkaufschancen mit Phasen, Aktivitäten und Verknüpfung zu Kunde, Angebot und Vertrag.

Revision ID: 0036
Revises: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE leads (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        number          INTEGER NOT NULL,
        title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        company         TEXT,
        contact_name    TEXT,
        email           TEXT,
        phone           TEXT,
        source          TEXT,
        stage           TEXT NOT NULL DEFAULT 'new' CHECK (stage IN ('new', 'contacted', 'qualified', 'offer', 'won', 'lost')),
        value           NUMERIC(19,2) NOT NULL DEFAULT 0 CHECK (value >= 0),
        recurring       BOOLEAN NOT NULL DEFAULT false,
        probability     INTEGER CHECK (probability IS NULL OR probability BETWEEN 0 AND 100),
        expected_close  DATE,
        owner_id        UUID REFERENCES shareholders (id),
        customer_id     UUID REFERENCES customers (id),
        offer_id        UUID REFERENCES offers (id),
        lost_reason     TEXT,
        notes           TEXT,
        position        DOUBLE PRECISION NOT NULL DEFAULT 0,
        won_at          TIMESTAMPTZ,
        lost_at         TIMESTAMPTZ,
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number)
    )
    """,
    "CREATE INDEX leads_stage_idx ON leads (tenant_id, stage, position)",
    """
    CREATE TABLE lead_activities (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   UUID NOT NULL,
        lead_id     UUID NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
        kind        TEXT NOT NULL CHECK (kind IN ('note', 'call', 'email', 'meeting', 'task', 'system')),
        text        TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 5000),
        due_date    DATE,
        done_at     TIMESTAMPTZ,
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX lead_activities_lead_idx ON lead_activities (lead_id, created_at DESC)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
