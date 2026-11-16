# Belege und Lieferanten
> Belegpostfach, Handy-Erfassung, Lieferanten-Regeln, KI-Erkennung und das Buchen per Klick.

## Wie Belege hereinkommen
- **Belegpostfach:** Rechnungen an euer Belegpostfach (IMAP) werden alle 15 Minuten abgeholt. E-Rechnungen (ZUGFeRD/XRechnung)
  werden sofort exakt gelesen, andere PDFs und Fotos von der KI. Verarbeitete Mails wandern in den Ordner „selbstlite-verarbeitet“.
- **Handy:** Unter **Beleg erfassen** (Kamerasymbol) fotografiert ihr Kassenbons und Rechnungen; das Bild wird verkleinert hochgeladen.
- **Upload:** Unter **Belege** per Datei auswählen.

## Lieferanten-Regeln
Jeder Lieferant hat eine Regel: Standardkategorie, Standardkonto und Steuerfall. Hat ein Lieferant ein Standardkonto, werden
neue Belege von ihm automatisch kontiert und stehen unter **Bereit zum Buchen**. Neue Lieferanten aus dem Postfach erscheinen
zuerst unter **Bitte prüfen** – einmal einsortieren, danach läuft es automatisch. Für SEPA-Überweisungen hinterlegt ihr hier auch
die IBAN.

## Buchen per Klick
Gebucht wird nie automatisch. **Bereit zum Buchen** zeigt Belege, bei denen alles passt; mit einem Klick (einzeln oder alle)
werden sie gebucht. Hinweise erscheinen bei Auffälligkeiten, etwa wenn ein Betrag stark vom üblichen Wert des Lieferanten
abweicht oder ein Gegenstand über 800 € netto als Anlagegut gelten könnte.

## Die KI
Die KI läuft lokal auf eurem Server (Ollama mit Qwen 2.5 VL) und liest nur aus: Lieferant, Datum, Beträge, Steuer, Rechnungsnummer.
Die Kontierung kommt aus euren Lieferanten-Regeln. Ohne KI funktioniert alles weiter – ihr tragt die Werte dann selbst ein.
