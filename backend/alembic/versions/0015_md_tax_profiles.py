"""Lohnsteuer-/SV-Merkmale je Geschäftsführer (versioniert).

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    """
    CREATE TABLE md_tax_profiles (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        shareholder_id     UUID NOT NULL REFERENCES shareholders (id),
        valid_from         DATE NOT NULL,
        tax_class          INT NOT NULL CHECK (tax_class BETWEEN 1 AND 6),
        child_allowances   NUMERIC(3,1) NOT NULL DEFAULT 0 CHECK (child_allowances BETWEEN 0 AND 20 AND child_allowances * 2 = trunc(child_allowances * 2)),
        church_tax         BOOLEAN NOT NULL DEFAULT false,
        state              CHAR(2) NOT NULL DEFAULT 'NW' CHECK (state IN ('BW','BY','BE','BB','HB','HH','HE','MV','NI','NW','RP','SL','SN','ST','SH','TH')),
        birth_year         INT CHECK (birth_year IS NULL OR birth_year BETWEEN 1930 AND 2015),
        children_under_25  INT NOT NULL DEFAULT 0 CHECK (children_under_25 BETWEEN 0 AND 20),
        health_insurance   TEXT NOT NULL DEFAULT 'gkv' CHECK (health_insurance IN ('gkv', 'pkv')),
        health_insurer     TEXT CHECK (health_insurer IS NULL OR length(health_insurer) <= 100),
        kv_additional_rate NUMERIC(4,2) CHECK (kv_additional_rate IS NULL OR kv_additional_rate BETWEEN 0 AND 6),
        pkv_premium        NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (pkv_premium BETWEEN 0 AND 5000),
        created_by         TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (shareholder_id, valid_from)
    )
    """,
    "CREATE TRIGGER md_tax_profiles_immutable BEFORE UPDATE OR DELETE ON md_tax_profiles FOR EACH ROW EXECUTE FUNCTION forbid_modification()",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
