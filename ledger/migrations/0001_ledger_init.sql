-- selbstlite Ledger: festgeschriebene, unveränderliche Datensätze (GoBD)
-- Ausgeführt vom Rust-Dienst mit der Rolle selbstlite_migrator (search_path = ledger).

CREATE SCHEMA IF NOT EXISTS ledger;

-- ───────────────────────── Hash-Kette ─────────────────────────
CREATE TABLE ledger.chain_records (
    seq               BIGINT      PRIMARY KEY CHECK (seq > 0),
    id                UUID        NOT NULL UNIQUE,
    tenant_id         UUID        NOT NULL,
    record_type       TEXT        NOT NULL
                      CHECK (length(record_type) <= 64
                             AND record_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$'),
    subject_id        UUID,
    number_range_key  TEXT,
    document_number   TEXT        CHECK (document_number IS NULL OR length(document_number) BETWEEN 1 AND 36),
    payload           JSONB       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    canonical_payload TEXT        NOT NULL,
    prev_hash         TEXT        NOT NULL CHECK (prev_hash ~ '^[0-9a-f]{64}$'),
    hash              TEXT        NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$'),
    sealed_at         TIMESTAMPTZ NOT NULL,
    sealed_by         TEXT        NOT NULL CHECK (length(sealed_by) BETWEEN 1 AND 200),
    CONSTRAINT number_range_and_number_together CHECK ((number_range_key IS NULL) = (document_number IS NULL)),
    CONSTRAINT document_number_unique UNIQUE (tenant_id, number_range_key, document_number)
);

CREATE INDEX chain_records_tenant_type_idx ON ledger.chain_records (tenant_id, record_type);
CREATE INDEX chain_records_subject_idx ON ledger.chain_records (subject_id) WHERE subject_id IS NOT NULL;

-- ───────────────────────── Nummernkreise ─────────────────────────
CREATE TABLE ledger.number_ranges (
    tenant_id  UUID        NOT NULL,
    range_key  TEXT        NOT NULL CHECK (range_key ~ '^[a-z][a-z0-9_]{0,31}$'),
    pattern    TEXT        NOT NULL CHECK (length(pattern) BETWEEN 1 AND 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, range_key)
);

CREATE TABLE ledger.number_counters (
    tenant_id  UUID   NOT NULL,
    range_key  TEXT   NOT NULL,
    year       INT    NOT NULL CHECK (year = 0 OR year BETWEEN 2000 AND 2100),
    last_value BIGINT NOT NULL CHECK (last_value > 0),
    PRIMARY KEY (tenant_id, range_key, year),
    FOREIGN KEY (tenant_id, range_key) REFERENCES ledger.number_ranges (tenant_id, range_key)
);

-- ───────────────────────── Periodensperren ─────────────────────────
CREATE TABLE ledger.locked_periods (
    tenant_id UUID   NOT NULL,
    period    TEXT   NOT NULL CHECK (period ~ '^(20[0-9]{2}|2100)-(0[1-9]|1[0-2])$'),
    chain_seq BIGINT NOT NULL UNIQUE REFERENCES ledger.chain_records (seq),
    PRIMARY KEY (tenant_id, period)
);

-- ───────────────────────── Unveränderbarkeit ─────────────────────────
CREATE FUNCTION ledger.forbid_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger.%: % ist verboten – festgeschriebene Daten sind unveränderlich', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

-- Zähler dürfen nur um genau 1 steigen; Schlüssel unveränderlich; Löschen verboten.
CREATE FUNCTION ledger.guard_number_counter() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.tenant_id = OLD.tenant_id
       AND NEW.range_key = OLD.range_key
       AND NEW.year = OLD.year
       AND NEW.last_value = OLD.last_value + 1 THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'ledger.number_counters: % nicht erlaubt (Zähler dürfen nur um 1 steigen)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER chain_records_immutable BEFORE UPDATE OR DELETE ON ledger.chain_records
    FOR EACH ROW EXECUTE FUNCTION ledger.forbid_modification();
CREATE TRIGGER chain_records_no_truncate BEFORE TRUNCATE ON ledger.chain_records
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.forbid_modification();

CREATE TRIGGER number_ranges_immutable BEFORE UPDATE OR DELETE ON ledger.number_ranges
    FOR EACH ROW EXECUTE FUNCTION ledger.forbid_modification();
CREATE TRIGGER number_ranges_no_truncate BEFORE TRUNCATE ON ledger.number_ranges
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.forbid_modification();

CREATE TRIGGER number_counters_guard BEFORE UPDATE OR DELETE ON ledger.number_counters
    FOR EACH ROW EXECUTE FUNCTION ledger.guard_number_counter();
CREATE TRIGGER number_counters_no_truncate BEFORE TRUNCATE ON ledger.number_counters
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.forbid_modification();

CREATE TRIGGER locked_periods_immutable BEFORE UPDATE OR DELETE ON ledger.locked_periods
    FOR EACH ROW EXECUTE FUNCTION ledger.forbid_modification();
CREATE TRIGGER locked_periods_no_truncate BEFORE TRUNCATE ON ledger.locked_periods
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.forbid_modification();

-- ───────────────────────── Rechte ─────────────────────────
REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON FUNCTION ledger.forbid_modification(), ledger.guard_number_counter() FROM PUBLIC;

GRANT SELECT, INSERT ON ledger.chain_records, ledger.number_ranges, ledger.locked_periods TO selbstlite_ledger;
GRANT SELECT, INSERT, UPDATE ON ledger.number_counters TO selbstlite_ledger;

-- Backend darf festgeschriebene Daten nur lesen
GRANT SELECT ON ledger.chain_records, ledger.locked_periods, ledger.number_ranges TO selbstlite_app;
