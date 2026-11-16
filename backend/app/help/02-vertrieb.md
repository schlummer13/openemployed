# Vertrieb, Kunden und Angebote
> Pipeline mit Leads, Kundenstamm (auch Ausland), Angebote mit Online-Annahme, Verträge und Kundenportal.

## Pipeline
Unter **Pipeline** verwaltet ihr Verkaufschancen (Leads) in den Phasen *Neu → Kontaktiert → Qualifiziert → Angebot → Gewonnen/Verloren*.
Karten lassen sich per Drag & Drop verschieben. Oben seht ihr das offene Volumen, das gewichtete Volumen (Wert × Wahrscheinlichkeit
der Phase oder eure eigene Angabe), die gewonnenen Aufträge des Jahres und die Abschlussquote.

So wird aus einem Lead ein Auftrag:
1. Lead anlegen – Titel, Firma, Ansprechpartner, Volumen (optional monatlich wiederkehrend), Verantwortliche Person.
2. Aktivitäten festhalten – Notiz, Anruf, E-Mail, Termin oder Aufgabe mit Fälligkeit. Überfällige Aufgaben werden rot markiert.
3. **Kunde anlegen** – mit Anschrift direkt aus dem Lead.
4. **Angebot erstellen** – es entsteht ein Angebotsentwurf mit dem Volumen als Position; der Lead springt auf „Angebot“.
5. Wird das Angebot angenommen, steht der Lead automatisch auf **Gewonnen**; wird es abgelehnt, auf **Verloren**.

Verlorene Leads brauchen einen Grund (z. B. Preis, Wettbewerber) – das macht die Auswertung später aussagekräftig.

## Kunden
Kunden sind Firmen (B2B) oder Privatpersonen (B2C). Kunden werden nie gelöscht, nur archiviert (Aufbewahrungspflicht).
- **Land und Währung:** Kunden im Ausland mit eigener Postleitzahl-Schreibweise; Rechnungswährung z. B. USD oder CHF.
- **USt-IdNr.:** Für EU-Firmenkunden Pflicht für steuerfreie Leistungen; das Länderpräfix wird geprüft.
- **E-Rechnungsformat:** ZUGFeRD/Factur-X (Standard) oder XRechnung 3.0 für Behörden – dann ist die Leitweg-ID Pflicht.
- **Peppol-ID:** für den Versand über das Peppol-Netz, z. B. `0204:991-12345-67`.
- **Kundenportal:** Kunden sehen ihre Rechnungen, Angebote und Verträge, laden PDFs/XML herunter, nehmen Angebote an und kündigen.

## Angebote
Angebote haben eine Gültigkeit, Positionen wie Rechnungen und können per E-Mail versendet werden. Kunden nehmen sie im Portal
an (mit Namen) oder ihr tragt die Annahme ein. Aus einem angenommenen Angebot wird mit einem Klick eine Rechnung oder ein Vertrag.
Optional erinnert OpenEmployed Kunden einmal an offene Angebote (Automatisierung „Angebots-Erinnerung“).

## Verträge
Verträge bilden wiederkehrende Leistungen ab (z. B. Software-Miete monatlich). Die Automatisierung „Vertragsabrechnung“ erstellt
fällige Rechnungen selbstständig. Kündigungen kommen über das öffentliche Kündigungsformular oder das Portal herein und werden von
euch bestätigt.
