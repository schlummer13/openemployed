-- Abschlussperiode „JJJJ-13“ für Abschlussbuchungen (Abschreibung, Rückstellungen, Abgrenzungen zum 31.12.).
-- Sie ist unabhängig von der Umsatzsteuer-Sperre des Dezembers und wird beim Jahresabschluss selbst festgeschrieben.
ALTER TABLE ledger.locked_periods DROP CONSTRAINT locked_periods_period_check;
ALTER TABLE ledger.locked_periods ADD CONSTRAINT locked_periods_period_check
    CHECK (period ~ '^(20[0-9]{2}|2100)-(0[1-9]|1[0-3])$');
