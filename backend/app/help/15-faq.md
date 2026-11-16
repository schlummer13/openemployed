# Häufige Fragen
> Antworten auf typische Fragen und ein kleines Glossar.

## Kann ich eine Rechnung nachträglich ändern?
Nein – ausgestellte Rechnungen sind festgeschrieben (GoBD). Erstellt eine Stornorechnung und danach eine neue Rechnung.

## Warum wird mein Beleg nicht automatisch gebucht?
Gebucht wird immer per Klick. Steht er unter „Bitte prüfen“, fehlt beim Lieferanten noch das Standardkonto.

## Die KI erkennt einen Beleg falsch.
Korrigiert die Werte im Beleg. Die KI liest nur aus; die Kontierung bestimmen eure Lieferanten-Regeln.

## Wie bekomme ich meine Daten aus Lexoffice oder sevDesk?
Über **Umstieg & Import**: Eröffnungsbilanz zum Stichtag, Kunden- und Lieferantenlisten als CSV, Buchungen als DATEV-Stapel.

## Wie gebe ich meiner Steuerkanzlei Zugriff?
Benutzer einladen mit der Rolle „Steuerberater (nur lesen)“ oder einen DATEV-Export senden.

## Rechnungen an Behörden?
Kunde auf XRechnung umstellen und die Leitweg-ID eintragen; Versand per E-Mail, Portal-Upload oder Peppol.

## Was passiert, wenn der Server ausfällt?
Die tägliche Sicherung und die Außer-Haus-Kopie stellen den Stand wieder her (`scripts/restore.sh`). Danach die Integritätsprüfung ausführen.

## Glossar
- **GoBD:** Grundsätze zur ordnungsmäßigen Führung von Büchern in elektronischer Form.
- **ELStAM:** Elektronische Lohnsteuerabzugsmerkmale (Steuerklasse, Freibeträge) vom Finanzamt.
- **Leitweg-ID:** Adresse öffentlicher Auftraggeber für E-Rechnungen.
- **Reverse Charge:** Der Leistungsempfänger schuldet die Umsatzsteuer.
- **SKR03/SKR04:** Standardkontenrahmen der DATEV.
- **Festschreibung:** unveränderliche Speicherung von Buchungen und Zeiträumen.
