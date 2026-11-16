"""Fundament: Firmenstammdaten (versioniert) und Audit-Log (nur anhängen).

Revision ID: 0001
Revises:
Create Date: 2026-11-14

Hinweis: asyncpg führt pro Aufruf nur genau einen SQL-Befehl aus – deshalb eine Liste von Befehlen.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    # Firmenstammdaten: jede Änderung ist eine neue Version (gültig ab), alte Versionen bleiben erhalten,
    # damit bereits ausgestellte Dokumente die damaligen Pflichtangaben behalten (§ 14 UStG, § 35a GmbHG).
    """
    CREATE TABLE company_profiles (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          UUID NOT NULL,
        valid_from         DATE NOT NULL,
        legal_name         TEXT NOT NULL CHECK (length(legal_name) BETWEEN 1 AND 200),
        legal_form         TEXT NOT NULL CHECK (legal_form IN ('UG (haftungsbeschränkt)', 'GmbH')),
        street             TEXT NOT NULL,
        postal_code        TEXT NOT NULL CHECK (postal_code ~ '^[0-9]{5}$'),
        city               TEXT NOT NULL,
        country_code       CHAR(2) NOT NULL DEFAULT 'DE',
        register_court     TEXT,
        register_number    TEXT,
        managing_directors TEXT[] NOT NULL DEFAULT '{}',
        tax_number         TEXT,
        vat_id             TEXT CHECK (vat_id IS NULL OR vat_id ~ '^DE[0-9]{9}$'),
        iban               TEXT,
        bic                TEXT,
        bank_name          TEXT,
        email              TEXT,
        phone              TEXT,
        website            TEXT,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by         TEXT NOT NULL,
        UNIQUE (tenant_id, valid_from),
        -- Rechtsform muss im Firmennamen stehen (UG: Pflichtzusatz § 5a Abs. 1 GmbHG)
        CHECK (position(legal_form IN legal_name) > 0)
    )
    """,
    # Unveränderlichkeit: Korrekturen nur über neue Versionen bzw. neue Einträge
    """
    CREATE FUNCTION forbid_modification() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '%: % ist verboten – Datensätze sind unveränderlich', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    """
    CREATE TRIGGER company_profiles_immutable BEFORE UPDATE OR DELETE ON company_profiles
        FOR EACH ROW EXECUTE FUNCTION forbid_modification()
    """,
    # Audit-Log: wer hat wann was geändert (GoBD-Nachvollziehbarkeit). Nur anhängen.
    """
    CREATE TABLE audit_log (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id   UUID NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        actor       TEXT NOT NULL,
        actor_type  TEXT NOT NULL CHECK (actor_type IN ('user', 'portal_user', 'system', 'job', 'webhook')),
        action      TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id   TEXT,
        before      JSONB,
        after       JSONB,
        ip          INET,
        user_agent  TEXT
    )
    """,
    "CREATE INDEX audit_log_entity_idx ON audit_log (tenant_id, entity_type, entity_id)",
    "CREATE INDEX audit_log_time_idx ON audit_log (tenant_id, occurred_at)",
    """
    CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION forbid_modification()
    """,
    """
    CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION forbid_modification()
    """,
    # Backend darf Stammdaten-Versionen und Audit-Einträge nur anlegen und lesen
    "REVOKE UPDATE, DELETE, TRUNCATE ON company_profiles, audit_log FROM selbstlite_app",
    # Versionstabelle von Alembic geht das Backend nichts an
    "REVOKE ALL ON alembic_version FROM selbstlite_app",
    "REVOKE ALL ON FUNCTION forbid_modification() FROM PUBLIC",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Fundament-Migration ist nicht umkehrbar (Aufbewahrungspflicht)")
