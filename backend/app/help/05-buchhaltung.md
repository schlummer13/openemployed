# Buchhaltung
> Journal, Kontenrahmen, Bankabgleich, Anlagen, Eröffnungsbilanz, Import und DATEV.

## Grundprinzip
OpenEmployed bucht doppelt nach SKR03 oder SKR04. Ausgangsrechnungen, Zahlungen, gebuchte Belege, Lohn, Reisekosten und
Abschreibungen erzeugen die Buchungssätze nach festen Regeln. Jede Buchung wird im Ledger festgeschrieben (lückenlose Nummer,
Hash-Kette). Manuelle Buchungen sind für Sonderfälle da.

## Bank
Kontoauszüge importiert ihr als CAMT.053, MT940 oder CSV, alternativ per FinTS direkt von der Bank. Eingänge werden offenen
Rechnungen zugeordnet (eindeutige Treffer automatisch), Abbuchungen gebuchten Eingangsrechnungen. Fremdwährungsrechnungen ordnet
ihr manuell zu – die Kursdifferenz bucht OpenEmployed selbst.

## Anlagen
Gebuchte Belege auf Anlagekonten erscheinen als Vorschlag für das Anlagenverzeichnis. Abschreibung linear, digitale Wirtschaftsgüter
(1 Jahr), Sammelposten (5 Jahre) oder GWG bis 800 €. Der Abschreibungslauf bucht zum 31.12. in die Abschlussperiode.

## Umstieg & Import
Unter **Umstieg & Import**:
- **Eröffnungsbilanz:** Schlusssalden je Bestandskonto (Aktiva im Soll, Passiva im Haben) plus offene Posten. Gebucht wird nur,
  wenn Soll und Haben übereinstimmen – einmalig, gegen die Saldenvortragskonten 9000/9008/9009.
- **DATEV-Buchungsstapel:** EXTF-Datei aus DATEV, Lexoffice, sevDesk oder dem Steuerbüro. BU-Schlüssel und Automatikkonten werden
  in Netto und Steuer aufgeteilt, Debitoren/Kreditoren auf die Sammelkonten gebucht.
- **Kunden und Lieferanten:** CSV-Export eurer bisherigen Software; Spalten werden erkannt.
Jeder Import hat eine Vorschau mit Fehlerliste. Die gleiche Datei kann nicht zweimal übernommen werden.

## DATEV-Export und Steuerberater
Der DATEV-Export liefert Buchungsstapel für die Kanzlei. Gebt der Kanzlei einen eigenen Zugang mit der Rolle
**Steuerberater (nur lesen)** – sie sieht alles und kann exportieren, aber nichts ändern.
