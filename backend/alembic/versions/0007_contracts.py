"""Verträge mit wiederkehrender Abrechnung (Phase 1e).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "CREATE SEQUENCE contract_number_seq START 1001",
    """
    CREATE TABLE contracts (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        UUID NOT NULL,
        number           TEXT NOT NULL DEFAULT ('V-' || nextval('contract_number_seq')),
        customer_id      UUID NOT NULL REFERENCES customers (id),
        title            TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'ended')),
        start_date       DATE NOT NULL,
        min_term_months  INT NOT NULL DEFAULT 0 CHECK (min_term_months BETWEEN 0 AND 120),
        notice_months    INT NOT NULL DEFAULT 1 CHECK (notice_months BETWEEN 0 AND 24),
        renewal          TEXT NOT NULL DEFAULT 'monthly' CHECK (renewal IN ('none', 'monthly', 'term')),
        billing_interval TEXT NOT NULL DEFAULT 'monthly' CHECK (billing_interval IN ('monthly', 'quarterly', 'yearly')),
        billing_mode     TEXT NOT NULL DEFAULT 'advance' CHECK (billing_mode IN ('advance', 'arrears')),
        auto_issue       BOOLEAN NOT NULL DEFAULT true,
        end_date         DATE,
        notice_received  DATE,
        notes            TEXT,
        activated_at     TIMESTAMPTZ,
        activated_by     TEXT,
        created_by       TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number),
        CHECK (end_date IS NULL OR end_date >= start_date)
    )
    """,
    "CREATE INDEX contracts_tenant_status_idx ON contracts (tenant_id, status)",
    """
    CREATE TABLE contract_lines (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        contract_id UUID NOT NULL REFERENCES contracts (id) ON DELETE CASCADE,
        position    INT NOT NULL CHECK (position BETWEEN 1 AND 100),
        product_id  UUID REFERENCES products (id),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 1000),
        quantity    NUMERIC(19,4) NOT NULL CHECK (quantity > 0),
        unit_code   TEXT NOT NULL,
        unit_price  NUMERIC(19,4) NOT NULL CHECK (unit_price >= 0),
        tax_case    TEXT NOT NULL,
        UNIQUE (contract_id, position)
    )
    """,
    # Jeder Abrechnungszeitraum wird genau einmal abgerechnet (Idempotenz des Abrechnungslaufs)
    """
    CREATE TABLE contract_billings (
        contract_id  UUID NOT NULL REFERENCES contracts (id),
        period_start DATE NOT NULL,
        period_end   DATE NOT NULL,
        invoice_id   UUID NOT NULL REFERENCES invoices (id),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (contract_id, period_start)
    )
    """,
    "REVOKE DELETE, TRUNCATE ON contracts, contract_billings FROM selbstlite_app",
    "REVOKE TRUNCATE ON contract_lines FROM selbstlite_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
