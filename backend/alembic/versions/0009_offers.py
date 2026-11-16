"""Angebote (Phase 2a).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14

Angebote sind keine Buchungsbelege; nach dem Versand werden sie dennoch nicht mehr verändert (Nachweis), Änderungen
erfolgen über eine neue Version (``revision_of``). Die Annahme wird mit Zeitpunkt, Name und Kanal protokolliert.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

STATEMENTS: list[str] = [
    "CREATE SEQUENCE offer_number_seq START 1",
    """
    CREATE TABLE offers (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       UUID NOT NULL,
        number          TEXT NOT NULL DEFAULT ('AN-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('offer_number_seq')::text, 4, '0')),
        customer_id     UUID NOT NULL REFERENCES customers (id),
        title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'accepted', 'declined', 'superseded')),
        price_mode      TEXT NOT NULL CHECK (price_mode IN ('net', 'gross')),
        valid_until     DATE NOT NULL,
        intro_text      TEXT CHECK (intro_text IS NULL OR length(intro_text) <= 5000),
        closing_text    TEXT CHECK (closing_text IS NULL OR length(closing_text) <= 5000),
        total_net       NUMERIC(19,2),
        total_tax       NUMERIC(19,2),
        total_gross     NUMERIC(19,2),
        revision_of     UUID REFERENCES offers (id),
        sent_at         TIMESTAMPTZ,
        decided_at      TIMESTAMPTZ,
        decided_by_name TEXT,
        decision_channel TEXT CHECK (decision_channel IS NULL OR decision_channel IN ('manual', 'portal', 'signature')),
        decision_note   TEXT,
        invoice_id      UUID REFERENCES invoices (id),
        contract_id     UUID REFERENCES contracts (id),
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, number)
    )
    """,
    "CREATE INDEX offers_tenant_status_idx ON offers (tenant_id, status)",
    """
    CREATE TABLE offer_lines (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        offer_id    UUID NOT NULL REFERENCES offers (id) ON DELETE CASCADE,
        position    INT NOT NULL CHECK (position BETWEEN 1 AND 500),
        product_id  UUID REFERENCES products (id),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 1000),
        quantity    NUMERIC(19,4) NOT NULL CHECK (quantity > 0),
        unit_code   TEXT NOT NULL,
        unit_price  NUMERIC(19,4) NOT NULL CHECK (unit_price >= 0),
        tax_case    TEXT NOT NULL,
        recurring   BOOLEAN NOT NULL DEFAULT false,
        UNIQUE (offer_id, position)
    )
    """,
    # Versendete Angebote: Inhalt unveränderlich (nur Status-/Entscheidungsfelder und Verknüpfungen änderbar)
    """
    CREATE FUNCTION guard_sent_offer() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'Versendetes Angebot % darf nicht gelöscht werden', OLD.number USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status <> 'draft' AND (to_jsonb(NEW) - ARRAY['status','decided_at','decided_by_name','decision_channel','decision_note',
                                      'invoice_id','contract_id','updated_at'])
           IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','decided_at','decided_by_name','decision_channel','decision_note',
                                      'invoice_id','contract_id','updated_at']) THEN
            RAISE EXCEPTION 'Versendetes Angebot % ist unveränderlich – neue Version anlegen', OLD.number
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$
    """,
    "CREATE TRIGGER offers_guard BEFORE UPDATE OR DELETE ON offers FOR EACH ROW EXECUTE FUNCTION guard_sent_offer()",
    """
    CREATE FUNCTION guard_sent_offer_lines() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE parent_status TEXT;
    BEGIN
        SELECT status INTO parent_status FROM offers WHERE id = COALESCE(NEW.offer_id, OLD.offer_id);
        IF parent_status IS NOT NULL AND parent_status <> 'draft' THEN
            RAISE EXCEPTION 'Positionen eines versendeten Angebots sind unveränderlich' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN COALESCE(NEW, OLD);
    END;
    $$
    """,
    "CREATE TRIGGER offer_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON offer_lines FOR EACH ROW EXECUTE FUNCTION guard_sent_offer_lines()",
    "REVOKE TRUNCATE ON offers, offer_lines FROM openemployed_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Nicht umkehrbar")
