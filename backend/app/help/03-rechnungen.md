# Rechnungen und E-Rechnung
> Rechnungen erstellen und festschreiben, ZUGFeRD, XRechnung, Peppol, Auslandsrechnungen, Fremdwährung, Zahlungen und Mahnungen.

## Rechnung erstellen
Unter **Rechnungen → Neu** wählt ihr den Kunden, den Leistungszeitraum und die Positionen (Produkte oder freie Positionen).
Firmenkunden sehen Nettopreise, Privatkunden Bruttopreise. Der Steuerfall jeder Position bestimmt Steuersatz und Pflichthinweise:
- **19 % / 7 %** – Inland.
- **EU-Leistung (Reverse Charge)** – sonstige Leistung an ein Unternehmen im EU-Ausland; der Kunde schuldet die Steuer.
- **ig. Lieferung (EU)** – steuerfreie Warenlieferung an ein Unternehmen im EU-Ausland.
- **Drittland (nicht steuerbar)** – Leistung an ein Unternehmen außerhalb der EU.

OpenEmployed prüft beim Ausstellen, ob Steuerfall, Land und USt-IdNr. des Kunden zusammenpassen.

## Freigeben und festschreiben
„Freigeben & festschreiben“ vergibt die fortlaufende Rechnungsnummer, schreibt die Rechnung unveränderlich fest, erzeugt die
E-Rechnung und prüft sie gegen die Norm EN 16931. Danach gibt es nur noch das Storno – Löschen ist ausgeschlossen.

## Formate
- **ZUGFeRD / Factur-X:** PDF mit eingebetteter XML (EN 16931) – für die meisten Geschäftskunden.
- **XRechnung 3.0:** reines XML für öffentliche Auftraggeber. Voraussetzungen: Leitweg-ID beim Kunden, Telefon und E-Mail im Firmenprofil.
  Download über den Button **XRechnung**; die Rechnungsmail hängt die XRechnung als Hauptanhang an, das PDF dient nur zur Ansicht.
- **Peppol:** mit eingerichtetem Access Point und Peppol-ID des Kunden über **Über Peppol senden**.

## Fremdwährung
Hat ein Kunde eine andere Rechnungswährung, wird die Rechnung in dieser Währung ausgestellt. Gebucht wird in Euro zum
EZB-Referenzkurs des Rechnungsdatums; die Umsatzsteuer steht zusätzlich in Euro auf der Rechnung. Kurse holt OpenEmployed werktags
automatisch, manuell unter *System & Integrationen → Währungen*. Beim Zahlungseingang tragt ihr den gutgeschriebenen Euro-Betrag
ein; die Differenz wird als Kursgewinn oder -verlust gebucht.

## Zahlungen und Mahnungen
Zahlungen kommen meist über den Kontoauszug-Import (Bankabgleich) – eindeutige Treffer ordnet OpenEmployed selbst zu. Manuell geht
es über **Zahlung erfassen** an der Rechnung. Der Mahnlauf erstellt Zahlungserinnerung, Mahnung und letzte Mahnung mit Verzugszinsen
und Pauschale (B2B 40 €) nach euren Einstellungen und versendet sie auf Wunsch automatisch.
