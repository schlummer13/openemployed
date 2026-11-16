# Verwaltung und Sicherheit
> Benutzer, Rollen, Anmeldung, Zwei-Faktor, mehrere Firmen, Einstellungen, Datensicherung und Updates.

## Rollen
Jede Rolle vergibt je **Bereich** eines von drei Rechten: **kein Zugriff**, **lesen** oder **bearbeiten**. Bereiche sind Übersicht,
Vertrieb & Kunden, Rechnungen, Belege, Buchhaltung, Zahlungsverkehr, Lohn & Personal, Steuern, Auswertungen, Archiv, Projekte & Zeiten,
Automatisierungen und Verwaltung. Was eine Rolle nicht lesen darf, verschwindet auch aus dem Menü.

Vorlagen:
- **Geschäftsführung:** alles, inkl. Benutzer, Einstellungen, API-Schlüssel und neuer Firmen – nicht änderbar.
- **Buchhaltung:** alle Fachbereiche, aber keine Verwaltung.
- **Steuerberater (nur lesen):** sieht alles, kann DATEV/GoBD exportieren, ändert nichts.
- **Mitarbeiter:** Projekte, Aufgaben und Zeiterfassung – keine Finanzen.

## Eigene Rollen anlegen
Unter **Benutzer & Rollen → Rollen & Rechte** seht ihr alle Rollen als Matrix. **Neue Rolle** öffnet ein Seitenpanel: Name, kurze
Beschreibung und je Bereich das Recht. Beispiele: „Vertrieb“ (Vertrieb bearbeiten, Rechnungen lesen), „Personal“ (Lohn & Personal
bearbeiten – darf damit Urlaub genehmigen und Stempelzeiten korrigieren), „Teamleitung“ (Lohn & Personal lesen = Team-Kalender sehen).
Die Vorlagen Buchhaltung, Steuerberater und Mitarbeiter lassen sich anpassen. Eine Rolle, die noch Benutzern zugewiesen ist, kann nicht
gelöscht werden.

Für **jede** angemeldete Person – unabhängig von der Rolle – erreichbar: eigenes Konto, **Stempeluhr**, **Urlaub & Abwesenheit** und
das Handbuch.

## Benutzer einladen
Unter **Benutzer & Rollen → Einladen**: E-Mail, Name, Rolle. Die Person bekommt einen Link (7 Tage gültig), legt ein Passwort fest
oder meldet sich direkt mit Google/Microsoft an. Benutzer werden gesperrt statt gelöscht; gesperrte Benutzer verlieren sofort alle Sitzungen.
Mindestens eine Person bleibt immer Geschäftsführung.

## Anmeldung und Zwei-Faktor
Möglich sind Google, Microsoft Entra ID, beliebige OIDC-Anbieter und E-Mail + Passwort. Unter **Mein Konto** richtet jede Person
die Zwei-Faktor-Anmeldung ein (QR-Code mit Google/Microsoft Authenticator, 1Password …). Optional ist sie für alle Pflicht.
Nach fünf falschen Passwörtern ist das Konto 15 Minuten gesperrt.

## Mehrere Firmen
Über die Firmenauswahl oben in der Seitenleiste wechselt ihr zwischen Firmen und legt neue an. Jede Firma hat getrennte Kunden,
Rechnungsnummern, Buchhaltung, Lohn und Automatisierungen. Benutzerkonten, Anmeldeverfahren und Systemeinstellungen sind gemeinsam.
Zugriff auf eine weitere Firma vergibt deren Geschäftsführung mit eigener Rolle.

## Einstellungen
Unter **System & Integrationen → Einstellungen**: E-Mail, Anmeldung, Steuern, E-Rechnung (Peppol) und KI – ohne Bearbeitung der
`.env`. Werte aus der App haben Vorrang, Geheimnisse liegen verschlüsselt in der Datenbank und werden nie angezeigt.

## Datensicherung und Updates
Die Sicherung läuft täglich (30 tägliche, 12 monatliche, 10 jährliche Stände, jeweils geprüft). Unter **Version & Sicherung**:
Status, „Jetzt sichern“ und Download. Updates auf dem Server mit `make update` – vorher wird automatisch gesichert. Eine Kopie
außer Haus ist Pflicht.
