# Digitale Signatur und Formvorschriften
> Verträge über Documenso unterschreiben lassen: Angebote, Kundenverträge, Arbeitsverträge und sonstige Verträge – mit Prüfung der Schriftform.

## So funktioniert die Signatur
Verträge entstehen in OpenEmployed (Angebot, Kundenvertrag, Vertragsvorlage in der Personalakte oder hochgeladenes PDF) und
werden mit **Zur Signatur senden** an Documenso übergeben. Documenso hängt eine Signaturseite an und schickt die Anfrage per E-Mail:
zuerst an den Vertragspartner, danach an die Geschäftsführung (Gegenzeichnung). Nach der letzten Unterschrift kommt das signierte
PDF automatisch zurück: Angebote werden angenommen, Kundenverträge aktiviert, Personalakten-Dokumente als „signiert“ markiert und
sonstige Verträge revisionssicher im Archiv (Kategorie Vertrag, 10 Jahre) abgelegt. Voraussetzung: `DOCUMENSO_API_TOKEN` in der
`.env`. Dokumente, die ihr direkt in der Documenso-Oberfläche anlegt, kennt OpenEmployed nicht.

## Verbindung zu Documenso
Documenso läuft mit OpenEmployed zusammen und wird **beim Start automatisch verbunden**: OpenEmployed legt das Konto der
Geschäftsführung an (erste Person mit Rolle Geschäftsführung bzw. `DOCUMENSO_ADMIN_EMAIL`), erzeugt den API-Zugang für das Team
und richtet die Rückmeldung (Webhook) ein – ohne Token kopieren. Den Status seht ihr unter **System & Integrationen →
Einstellungen → Digitale Signatur**. Dort öffnet **Eigenes Documenso-Passwort festlegen** einen Einmal-Link (1 Stunde), mit dem ihr
euer Passwort in Documenso selbst wählt; danach meldet ihr euch unter der Documenso-Adresse an (lokal `sign.localhost`). Weitere
Personen ladet ihr in Documenso unter Organisation → Mitglieder ein.

## Verträge in Documenso anlegen und bearbeiten
Ihr könnt Verträge auch direkt in Documenso erstellen, Felder platzieren, Empfänger festlegen und versenden. Alles, was im
**Team** angelegt oder geändert wird, erscheint sofort unter **Verträge → Sonstige Verträge & Signatur** (Kennzeichen „in
Documenso angelegt“) mit Titel, Empfängern und Status; die Formprüfung läuft auch hier (Hinweis z. B. bei Mietverträgen).
Sobald alle unterschrieben haben, holt OpenEmployed das signierte PDF und legt es revisionssicher im Archiv ab. Zusätzlich gleicht
OpenEmployed alle 15 Minuten ab, falls eine Rückmeldung verloren ging. Dokumente im persönlichen Bereich eines anderen Kontos
sind nicht sichtbar – im Team anlegen.

## Vorlagen aus Documenso
Vorlagen gestaltet ihr in Documenso (PDF, Felder, Rollen wie „Mitarbeiter“ und „Geschäftsführung“). In OpenEmployed nutzt ihr
sie über **Aus Documenso-Vorlage** – unter Sonstige Verträge oder direkt in der **Personalakte** eines Mitarbeiters: Empfänger je
Rolle wählen (der Mitarbeiter ist vorbelegt), Titel anpassen, versenden. Der Stand erscheint unter Sonstige Verträge; das
signierte Exemplar landet im Archiv und – bei Mitarbeitern – in der Personalakte (für den Mitarbeiter sichtbar). Bei befristeten
Mitarbeitern fragt OpenEmployed wegen der Schriftform nach.

## Sonstige Verträge
Unter **Verträge → Sonstige Verträge & Signatur** ladet ihr ein PDF hoch (ohne Kennwortschutz), wählt die Vertragsart, den
Vertragspartner und die unterschreibende Person. Typische Fälle: Rahmen- und Lieferverträge, Dienstleister, Software/SaaS, NDA,
Auftragsverarbeitung (AVV), Vertragsänderungen.

## Welche Verträge digital gehen
Documenso erzeugt einfache bzw. fortgeschrittene elektronische Signaturen. Das genügt für **formfreie** Verträge und die
**Textform**, ersetzt aber nicht die **Schriftform** (§ 126 BGB) – die ersetzt nur die eigenhändige Unterschrift oder eine
**qualifizierte** elektronische Signatur. OpenEmployed prüft deshalb vor jedem Versand die Art (auch anhand des Titels):

| Art | Ergebnis | Grund |
|---|---|---|
| Rahmen-/Liefer-/Dienstvertrag, NDA, Software, AVV, unbefristeter Arbeitsvertrag | digital wirksam | formfrei bzw. Textform |
| Befristeter Arbeitsvertrag | qualifiziert oder Papier | § 14 Abs. 4 TzBfG – sonst gilt er als unbefristet |
| Nachvertragliches Wettbewerbsverbot | qualifiziert oder Papier (im Zweifel Papier) | § 74 HGB – sonst nichtig |
| Miet-/Pachtvertrag über ein Jahr | qualifiziert oder Papier | § 550 BGB – sonst unbefristet und kündbar |
| Verbraucherdarlehen | qualifiziert oder Papier | § 492 BGB |
| Kündigung, Aufhebungsvertrag (Arbeitsverhältnis) | nur Papier (Original) | § 623 BGB – elektronische Form ausgeschlossen |
| Bürgschaft, Schuldanerkenntnis | nur Papier (Original) | §§ 766, 780, 781 BGB |
| Grundstücke, GmbH-Anteile | gesperrt | notarielle Beurkundung |

## Unterschrift einholen: die vier Wege
**Unterschrift einholen** (unter Sonstige Verträge und in der Personalakte) zeigt je nach Vertragsart, welche Wege möglich sind,
und empfiehlt den passenden:

- **Digital über Documenso** – für formfreie Verträge. Bei Schriftform-Verträgen nur als zusätzliche Kopie (mit Bestätigung).
- **Qualifiziert elektronisch** – über einen externen Vertrauensdiensteanbieter, wenn eingerichtet. Ersetzt die Schriftform
  (§ 126a BGB). Die unterschreibende Person identifiziert sich einmalig beim Anbieter (z. B. Online-Ausweis oder Video-Ident) und
  bestätigt die Unterschrift mit einem zweiten Faktor. OpenEmployed fragt den Stand alle 15 Minuten ab und archiviert das
  qualifiziert signierte PDF.
- **Ausdrucken und unterschreiben** – das PDF öffnet sich zum Drucken; danach den Scan des unterschriebenen Exemplars mit
  **Unterschriebenen Scan hochladen** ablegen. Das Original bleibt der Nachweis – in die Papierakte.
- **Per E-Mail zum Ausdrucken senden** – das PDF geht als Anhang an die unterschreibende Person, mit der Bitte, auszudrucken, zu
  unterschreiben und das Original an eure Adresse zu senden. Bei Kündigung und Aufhebungsvertrag ist die E-Mail nur eine
  Vorab-Information; wirksam wird erst das zugegangene Original.

Bei Erklärungen mit ausgeschlossener elektronischer Form (Kündigung, Aufhebungsvertrag, Bürgschaft) sind Documenso und die
qualifizierte Signatur gesperrt – auch eine QES würde hier nicht genügen.

## Qualifizierte Signatur einrichten (optional)
Unter **System & Integrationen → Einstellungen → Qualifizierte Signatur** wählt ihr den Anbieter und tragt API-Benutzer und
API-Schlüssel ein. Mitgeliefert ist die Anbindung an **Skribble** (QES nach EU-Recht); mit einem Demo-Schlüssel des Anbieters
könnt ihr den Ablauf vorher testen. Weitere Anbieter (z. B. D-Trust sign-me, Swisscom, Namirial) lassen sich ergänzen. Alternativ
bietet Documenso selbst qualifizierte Signaturen in der Enterprise-Version mit einem CSC-fähigen Anbieter an. Ohne Anbieter
funktionieren Ausdruck und E-Mail jederzeit.
