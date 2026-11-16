# Erste Schritte
> Installation, Einrichtungsassistent, Oberfläche und die wichtigsten Begriffe.

## Was ist OpenEmployed?
OpenEmployed (Open Source + Self-Employed) ist eine selbst gehostete Software für Selbstständige, UG und GmbH: Angebote,
Rechnungen und E-Rechnungen, Belege, doppelte Buchführung, Lohn, Steuern, Projekte und Automatisierungen in einem System.
Alle Daten liegen auf eurem eigenen Server. Buchungen werden nach GoBD festgeschrieben – unveränderlich, lückenlos nummeriert
und mit einer Hash-Kette gesichert.

## Installation
Auf einem Server mit Docker genügt `make install`. Das Skript erzeugt die `.env` mit zufälligen Geheimnissen, baut alle Dienste,
spielt die Datenbank-Migrationen ein und nennt danach die Adresse. Zum Ausprobieren auf dem eigenen Rechner: `make install-lokal`
– dann landen alle E-Mails im Testpostfach unter http://localhost:8025 und Demodaten lassen sich mit `make demo` anlegen.

Für den Betrieb im Internet steht OpenEmployed hinter einem Reverse-Proxy (nginx-Vorlage unter `infra/nginx`). Die Dienste lauschen
nur auf 127.0.0.1.

## Einrichtungsassistent
Unter **Einrichtung** (Menü *System*) führt ein Assistent in sechs Schritten durch:
1. **Firma** – Name, Anschrift, Register, Steuernummer, USt-IdNr., Bankverbindung. Diese Angaben erscheinen auf jeder Rechnung.
2. **Steuern** – Bundesland des Finanzamts, monatliche oder vierteljährliche Umsatzsteuer-Voranmeldung, Gewerbesteuer-Hebesatz.
3. **E-Mail** – Anbieter wählen (Microsoft 365, Google, IONOS, STRATO …), Zugangsdaten eintragen, Testmail senden.
4. **Anmeldung & Team** – Passwort-Anmeldung, Zwei-Faktor-Pflicht, Rolle neuer Firmenkonten; Kolleginnen und Steuerkanzlei einladen.
5. **Bank & Daten** – Kontoauszug-Import, Umstieg aus anderer Software, Mitarbeiter, Gläubiger-ID für Lastschriften.
6. **Fertig** – die Übersicht zeigt weiter, was für den Produktivstart noch fehlt.

## Die Oberfläche
- **Seitenleiste:** Bereiche Vertrieb, Finanzen, Team und System. Oben die Firmenauswahl, wenn ihr mehrere Firmen verwaltet.
- **Suche (⌘K / Strg+K):** springt zu Kunden, Rechnungen, Seiten und Aktionen.
- **Zu erledigen:** sammelt alles, was eure Aufmerksamkeit braucht – überfällige Rechnungen, Belege zum Buchen, Fristen.
- **Hell/Dunkel:** Umschalter unten in der Seitenleiste.

## Wichtige Begriffe
- **Festschreiben:** Eine Buchung oder ein Zeitraum wird unveränderlich. Korrekturen erfolgen nur noch per Gegenbuchung oder Storno.
- **Entwurf:** Alles vor dem Festschreiben – frei änderbar und löschbar.
- **Storno:** Korrektur einer ausgestellten Rechnung durch eine Stornorechnung (Spiegelbild), nie durch Löschen.
- **Mandant / Firma:** Eine Installation kann mehrere Firmen mit strikt getrennten Daten verwalten.
