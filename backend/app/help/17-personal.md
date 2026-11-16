# Personal: Zeitkonto, On-/Offboarding, Personalakte
> Überstunden und Arbeitszeitkonto, Checklisten für Eintritt und Austritt, digitale Personalakte mit Verträgen und Signatur.

## Arbeitszeitkonto
Jeder Mitarbeiter hat ein Zeitkonto: **Soll** je Arbeitstag = Wochenstunden ÷ Arbeitstage; an Feiertagen und betrieblich freien
Tagen kein Soll (halber Tag = halbes Soll). **Ist** = Netto-Arbeitszeit aus der Stempeluhr. Urlaub, Krankheit, Sonderurlaub und
Berufsschule gelten als erfüllt; unbezahlter Urlaub, Elternzeit und Mutterschutz setzen das Soll auf 0; **Überstundenabbau** lässt
das Soll stehen und baut so den Saldo ab. Das Konto startet mit der ersten Stempelung oder am Datum „Zeitkonto ab“.

Beim Mitarbeiter legt ihr die **Überstunden-Regel** fest (Freizeitausgleich, Auszahlung, mit Gehalt abgegolten) und optional eine
**Grenze** – darüber erscheint ein Hinweis. Unter **Personalakte → Zeitkonto → Buchen**: **Übertrag** (z. B. aus dem Altsystem),
**Korrektur**, **Verfall** und **Auszahlung** – die Auszahlung erzeugt automatisch die Lohnposition „Überstundenvergütung“
(Stundenwert = Stundenlohn bzw. Monatsgehalt ÷ (Wochenstunden × 13/3), optional mit Zuschlag) im gewählten Abrechnungsmonat.
Mitarbeiter sehen ihr Konto unter **Stempeluhr → Mein Zeitkonto** und im Portal.

## On- und Offboarding
Beim Anlegen eines Mitarbeiters startet automatisch die **Onboarding-Checkliste**, beim ersten Eintragen eines Austrittsdatums die
**Offboarding-Checkliste** – jeweils mit Fristen relativ zum Ein-/Austritt, Verantwortlichen und Rechtsgrundlage:

- Onboarding: Arbeitsvertrag (Nachweisgesetz), Steuer-ID/ELStAM, SV-Nummer und Krankenkasse, **SV-Anmeldung binnen 6 Wochen**,
  Bankverbindung, Datenschutzverpflichtung, **Arbeitsschutz-Unterweisung**, Aufenthaltstitel, Arbeitsmittel, Zugänge, Einarbeitung,
  Gespräch vor Ende der Probezeit.
- Offboarding: Kündigung im Original (§ 623 BGB), Hinweis Arbeitsuchendmeldung, Resturlaub, Zeitkonto, **Arbeitszeugnis**,
  Urlaubsbescheinigung, Arbeitsbescheinigung, **SV-Abmeldung**, Lohnsteuerbescheinigung, Rückgabe Arbeitsmittel, Zugänge sperren,
  Löschfristen.

Überfällige und bald fällige Punkte erscheinen unter „Zu erledigen“. Die Vorlagen bearbeitet ihr unter **Mitarbeiter & Lohn →
Personal → Checklisten-Vorlagen** (eine Zeile je Punkt: Tage | Titel | Verantwortlich | Rechtsgrundlage).

## Personalakte
Je Mitarbeiter unter **Mitarbeiter & Lohn → Personalakte →**: Dokumente mit Kategorie (Arbeitsvertrag, Änderung, Bewerbung,
Zeugnis, Abmahnung, Ausweis/Aufenthaltstitel, Schulung, Kündigung …), Gültigkeit und Notiz. „Für Mitarbeiter sichtbar“ zeigt das
Dokument im Portal und unter „Meine Dokumente“. Zugriff nur mit Recht *Lohn & Personal*.

**Löschfristen (DSGVO):** Personalakten liegen bewusst nicht im unveränderlichen GoBD-Archiv. Mit dem Austritt bekommt jedes
Dokument eine Löschfrist (Ende des 3. Jahres nach Austritt, anpassbar); danach erscheint ein Hinweis. **Löschen** entfernt den
Inhalt endgültig – Titel, Prüfsumme und Grund bleiben als Nachweis. Lohnunterlagen bleiben zusätzlich revisionssicher im Archiv.

## Verträge aus Vorlagen
Unter **Personal → Vertragsvorlagen** pflegt ihr Vorlagen in einfachem Markdown mit Platzhaltern wie `{{mitarbeiter.name}}`,
`{{vertrag.beginn}}`, `{{vertrag.verguetung}}`, `{{vertrag.wochenstunden}}`, `{{vertrag.urlaubstage}}`. Ein Muster-Arbeitsvertrag
mit den Pflichtangaben nach dem Nachweisgesetz (inkl. Hinweis auf die Klagefrist) ist hinterlegt – vor der Nutzung anwaltlich
prüfen lassen. **Aus Vorlage** erzeugt ein PDF in der Personalakte; **Signieren lassen** schickt es über Documenso an den
Mitarbeiter und danach an die Geschäftsführung. Das signierte PDF erscheint automatisch in der Akte.
