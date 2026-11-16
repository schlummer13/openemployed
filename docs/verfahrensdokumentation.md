# Verfahrensdokumentation nach GoBD – OpenEmployed

> Stand: 14.11.2026 · Gilt für: Beispiel Software UG (haftungsbeschränkt), Düsseldorf · Verantwortlich: alle Geschäftsführer
> Diese Dokumentation beschreibt das DV-gestützte Buchführungsverfahren (BMF-Schreiben GoBD vom 28.11.2019, zuletzt
> geändert 2024/2025, Rz. 151 ff.). ⚠️ Vor Produktivstart mit dem Steuerberater durchgehen, unterschreiben und bei jeder
> wesentlichen Änderung fortschreiben (Änderungshistorie am Ende). Aufbewahrung: so lange wie die beschriebenen Unterlagen
> (bis zu 10 Jahre, § 147 Abs. 3 AO).

## 1. Allgemeine Beschreibung

| Punkt | Beschreibung |
|---|---|
| Unternehmen | UG (haftungsbeschränkt), 4 Gesellschafter-Geschäftsführer, Sitz Düsseldorf |
| Gewinnermittlung | Bilanzierung (Kaufmann kraft Rechtsform, §§ 238 ff. HGB); Umsatzsteuer nach vereinbarten Entgelten (Soll-Versteuerung) |
| Kontenrahmen | SKR03 oder SKR04 (einmalig festgelegt, danach gesperrt) |
| Software | OpenEmployed (Eigenentwicklung): Rechnungsstellung, Belegerfassung, Buchungsjournal, Festschreibung, DATEV-Übergabe |
| Steuerberater | erhält monatlich den DATEV-Export (EXTF) und erstellt Jahresabschluss und Steuererklärungen |
| Betrieb | eigener Server (Netcup), Docker; Zugriff nur über Google-Workspace-Konten der Geschäftsführer (keine Rollen, alle gleichberechtigt) |

## 2. Anwenderdokumentation (Abläufe)

### 2.1 Ausgangsrechnungen
1. Rechnungsentwurf anlegen (frei änderbar, keine Buchung).
2. **Freigabe**: Pflichtangaben-Prüfung (§ 14 Abs. 4 UStG), Rechnungsnummer aus lückenlosem Nummernkreis `RE-JJJJ-NNNNN`
   (vergeben vom Festschreibungsdienst), E-Rechnung ZUGFeRD/EN 16931 (XSD- und Schematron-geprüft), Festschreibung
   mit SHA-256-Hash. Danach ist die Rechnung unveränderlich.
3. Korrektur ausschließlich per **Stornorechnung** (vollständige Spiegelung, eigener Beleg, Bezug zur Originalrechnung).
4. Buchung im Journal automatisch nach Regel: Forderungen an Erlöse und Umsatzsteuer je Steuersatz.

### 2.2 Eingangsbelege
1. Eingang per Upload oder Belegpostfach (E-Mail). Originaldatei wird unverändert gespeichert (Hash, Unveränderbarkeit
   per Datenbank-Trigger), auch wenn noch nicht gebucht.
2. **Erkennung** (nur Lesen der Belegdaten): E-Rechnungen (XRechnung/ZUGFeRD) werden deterministisch aus dem XML gelesen;
   PDFs und Bilder liest eine lokale KI auf eigener Hardware (Lieferant, Datum, Rechnungsnummer, Beträge). Die Erkennung
   liefert nur Vorschläge und kontiert nicht.
3. **Kontierung über Lieferanten-Regeln:** Jeder Lieferant hat eine Regel aus Standardkonto (Aufwands- oder Anlagekonto),
   Kategorie und Steuerfall. Belege bekannter Lieferanten werden danach vorkontiert und als „bereit zum Buchen“ angezeigt.
   Ein **neuer** Lieferant aus dem Belegpostfach wird als „bitte einsortieren“ markiert; ein Geschäftsführer legt beim
   ersten Beleg Konto, Kategorie und Steuerfall fest („für diesen Lieferanten merken“). Erst danach werden weitere Belege
   dieses Lieferanten automatisch vorkontiert. Zulässig sind nur Aufwands- und Anlagekonten des Kontenrahmens.
4. **Prüfhinweise** verhindern die Sammelbuchung, bis sie geklärt sind: Beträge über 800 € netto bei Bürobedarf/GWG
   (Anlagevermögen prüfen, § 6 Abs. 2 EStG) und Beträge, die stark vom Üblichen des Lieferanten abweichen.
5. **Buchung nur per Klick** durch einen Geschäftsführer (einzeln oder als Sammelbuchung der ausgewählten, bereiten
   Belege): Festschreibung des Belegs (Hash) und Buchungssatz im Journal. Vorsteuer, Reverse Charge (§ 13b UStG),
   Bewirtung 70/30 (§ 4 Abs. 5 Nr. 2 EStG) und Gegenbuchung ergänzt das System nach festen Regeln; vor dem Schreiben wird
   geprüft: Konten existieren im Kontenrahmen, Beträge exakt, Soll = Haben. Es gibt keine automatische Buchung ohne Klick.
6. Korrektur ausschließlich per Stornobuchung (Gegenbuchung, Originalbeleg bleibt sichtbar).

### 2.3 Zahlungen
Zahlungseingänge werden manuell, per Kontoauszug (CAMT.053, MT940, CSV) oder Onlinebanking (FinTS/HBCI) erfasst. Automatische
Zuordnung nur bei eindeutiger Rechnungsnummer **und** exaktem Betrag; sonst Vorschlag mit Bestätigung. Teilzahlungen
werden nach § 367 BGB verrechnet. Zahlungen werden nicht gelöscht, sondern storniert.

### 2.3a Bankabgleich für Ausgaben
Abbuchungen werden gebuchten, offenen Eingangsrechnungen zugeordnet (exakter Betrag; bei Reverse Charge der
Nettobetrag). Automatisch nur, wenn genau ein Beleg mit passendem Lieferantennamen bzw. Rechnungsnummer infrage
kommt; sonst Vorschlag mit Bestätigung. Die Zuordnung setzt das Zahlungsdatum am Beleg; die Zahlungsbuchung
(Verbindlichkeiten an Bank) entsteht als Regelbuchung.

### 2.3b Anlagenbuchhaltung
Gebuchte Belege auf Anlagekonten werden ins Anlagenverzeichnis übernommen (Nummer AV-NNNN, Konto, Anschaffungs-
datum, Anschaffungskosten netto, Nutzungsdauer, Methode). Abschreibung: linear monatsgenau (§ 7 Abs. 1 EStG),
digitale Wirtschaftsgüter im Anschaffungsjahr (BMF 22.02.2022), Sammelposten 5 × 20 % (§ 6 Abs. 2a EStG), GWG bis
800 € (§ 6 Abs. 2 EStG). Der jährliche Abschreibungslauf bucht je Anlagegut eine Abschlussbuchung zum 31.12.; danach
sind die Rechengrundlagen des Anlageguts unveränderlich, gebuchte Abschreibungen per Trigger gesperrt.

### 2.3c Jahresabschluss-Vorbereitung
Abschlussbuchungen (Abschreibung, Steuerrückstellungen, Rückstellung für Abschlusskosten, sonstige Rückstellungen,
Rechnungsabgrenzung) tragen das Datum 31.12. und gehören in die **Abschlussperiode JJJJ-13**. Diese ist unabhängig von
der Umsatzsteuer-Sperre des Dezembers und wird mit dem Jahresabschluss festgeschrieben; danach lehnen Festschreibungs-
dienst und Datenbank weitere Abschlussbuchungen ab. Rechnungsabgrenzungen werden am 1.1. des Folgejahres automatisch
aufgelöst. Bilanz und GuV werden als vereinfachter Entwurf (§§ 266, 275 HGB) erzeugt; das Übergabepaket an den
Steuerberater enthält Bilanz, GuV, Summen- und Saldenliste, Anlagenspiegel, Journal und Checkliste.

### 2.3d Zeiterfassung, Reisekosten
Erfasste Zeiten werden per Klick als Rechnungsentwurf an den Projektkunden abgerechnet (Freigabe wie jede Rechnung);
abgerechnete Zeiten sind gesperrt. Reisekosten der Geschäftsführer: Verpflegungspauschalen Inland (14 €/28 €, Kürzung
bei gestellten Mahlzeiten), Kilometerpauschale 0,30 €; gebucht als Verbindlichkeit gegenüber dem Geschäftsführer,
Auszahlung gegen Bank. Belege mit Vorsteuer (Hotel, Bahn) werden regulär als Beleg erfasst.

### 2.3e Umsatzsteuer-Voranmeldung über ELSTER
Der Datensatz wird aus den festgeschriebenen Kennzahlen erzeugt und über ERiC geprüft und übermittelt (nur
festgeschriebene Zeiträume; Testmodus mit Testmerker zuerst). Jede Prüfung/Übertragung wird mit Prüfsumme des
Datensatzes und Transferticket protokolliert.

### 2.3f Lohn und Gehalt
Mitarbeiterstamm mit ELStAM- und SV-Merkmalen; monatlicher Lohnlauf als Entwurf (beliebig neu berechenbar). Mit „Freigeben“
werden die Abrechnungen unveränderlich (Datenbank-Trigger) und die Lohnbuchung festgeschrieben (Personalaufwand an
Verbindlichkeiten Lohn, Lohnsteuer, Sozialversicherung). Zahlungen (Nettolöhne, Lohnsteuer, SV) werden je Zahlungsdatum gebucht.
Entgeltabrechnungen werden archiviert und Mitarbeitern im Portal bereitgestellt. Die Berechnung ist eine Näherung; maßgeblich
sind ELStAM und Beitragssätze – Abgleich mit dem Lohnbüro vor dem Produktivstart.

### 2.3g Zahlungsverkehr (SEPA)
Überweisungs- und Lastschriftdateien (pain.001/pain.008) werden erzeugt, protokolliert und archiviert; Freigabe und Ausführung
erfolgen ausschließlich im Online-Banking. Gebucht wird erst mit dem Kontoauszug (Bankabgleich) bzw. „als bezahlt buchen“.
Lastschriftmandate werden mit Referenz, Unterschriftsdatum und Erst-/Folgelastschrift geführt.

### 2.3h Umstieg und Datenimport
Eröffnungsbilanz einmalig gegen Saldenvortragskonten (9000/9008/9009), Bilanzgleichheit wird erzwungen. DATEV-Buchungsstapel,
Kunden- und Lieferantenlisten werden zweistufig (Vorschau, Übernahme) importiert; jede Datei ist über ihren SHA-256 protokolliert
und kann nicht doppelt übernommen werden.

### 2.3i Monatsabschluss
Checkliste je Monat (Kontoauszüge, Bankabgleich, Belege, Rechnungen, Lohnlauf, Vorauszahlungen, UStVA); danach Festschreibung
des Monats im Ledger. Offene Punkte können nur ausdrücklich bestätigt werden; die Bestätigung wird mit festgeschrieben.

### 2.3j Auslandsgeschäft und Fremdwährung
EU-Reverse-Charge, innergemeinschaftliche Lieferungen und Drittlandsleistungen werden gegen Land und USt-IdNr. des Kunden geprüft.
Rechnungen in Fremdwährung werden zum EZB-Referenzkurs des Rechnungsdatums in Euro gebucht; der Steuerbetrag steht zusätzlich in
Euro auf der Rechnung (§ 14 Abs. 4 Nr. 8 UStG). Kursdifferenzen beim Zahlungseingang werden separat gebucht.

### 2.3k E-Rechnung für öffentliche Auftraggeber
XRechnung 3.0 (CII) mit Leitweg-ID (Prüfziffer geprüft) als Käuferreferenz; Versand per E-Mail, Upload oder über einen
Peppol-Access-Point. Die XRechnung wird mit dem ZUGFeRD-PDF unveränderlich gespeichert und ihr Hash festgeschrieben.

### 2.3l Automatisierung und Schnittstellen
Eigene Workflows (Zeitplan oder Ereignis) und Webhooks greifen nur über die regulären Fachfunktionen ein – sie können nichts
buchen oder festschreiben, was nicht auch per Oberfläche möglich wäre, und jede Änderung landet im Audit-Log. Python-Schritte
laufen isoliert in einem eigenen Container ohne Zugriff auf Datenbank oder interne Netze.

### 2.3m Arbeitszeiterfassung und Abwesenheiten
Mitarbeiter stempeln selbst ein und aus (App oder Mitarbeiter-Portal; Zeitstempel = Serverzeit). Die gesetzliche Pause (§ 4 ArbZG,
§ 11 JArbSchG) wird je Tag automatisch abgezogen. Korrekturen nur durch Personen mit Recht „Lohn & Personal – bearbeiten“, mit
Pflichtbegründung und Audit-Eintrag (alter/neuer Wert). Die Aufzeichnungen dienen als Arbeitszeitnachweis (§ 16 Abs. 2 ArbZG,
§ 17 MiLoG für Minijobs) und als Grundlage der Stundenlohnabrechnung; Aufbewahrung mindestens 2 Jahre, als Lohnunterlage 6 Jahre.
Urlaubsanträge werden von berechtigten Rollen genehmigt oder abgelehnt; Antrag, Entscheidung, Entscheider und Zeitpunkt sind gespeichert.

### 2.3n Archivspiegelung und Datensicherung des Archivs
Alle Archivdokumente werden täglich auf mindestens ein externes Ziel gespiegelt; empfohlen ist S3-Speicher mit Object Lock
(COMPLIANCE, Sperrfrist = Aufbewahrungsfrist des Dokuments) in der EU. Täglich werden Hash-Kette und Prüfsummen im System sowie
eine Stichprobe der Kopien geprüft; Abweichungen werden protokolliert und neu gespiegelt. Ein vollständiger Export mit Manifest und
Offline-Prüfprogramm kann jederzeit erzeugt werden (Datenträgerüberlassung, Notfall).

### 2.3o Entgeltabrechnung und Personalakte
Lohnsteuer nach dem amtlichen Programmablaufplan des BMF (aus der offiziellen XML erzeugt, gegen die BMF-Prüfschnittstelle getestet).
Lohnpositionen, Pfändungen und Zeitkonto-Buchungen werden mit Benutzer und Zeitpunkt protokolliert; freigegebene Abrechnungen sind
unveränderlich und werden als PDF archiviert. SV-Meldungen, Beitragsnachweise und Lohnsteuerbescheinigungen werden vorbereitet,
die Übermittlung mit Datum und Referenz dokumentiert. Personalakten werden getrennt vom GoBD-Archiv geführt und nach Ablauf der
Löschfrist gelöscht (Löschnachweis mit Prüfsumme).

### 2.4 Zeitgerechte Erfassung (Rz. 46 ff.)
- Unbare Geschäftsvorfälle werden laufend, spätestens bis Ende des Folgemonats erfasst; das System erinnert an
  Belegentwürfe, die älter als 10 Tage sind, und an Kontoabbuchungen ohne Beleg.
- Kassenvorgänge: keine Barkasse.

## 3. Technische Systemdokumentation

### 3.1 Unveränderbarkeit (§ 146 Abs. 4 AO, Rz. 58 ff.)
Dreifache Absicherung:
1. **Festschreibungsdienst (Rust, eigener Dienst und eigene Datenbankrolle)**: Jeder festgeschriebene Datensatz
   (Rechnung, Storno, Beleg, Zahlung, Buchungssatz, Signatur, UStVA, DATEV-Export, Periodensperre) wird in einer
   Hash-Kette gespeichert: `hash = SHA-256(kanonisches JSON + vorheriger Hash)`. Änderungen und Löschungen sind per
   Trigger verboten – auch für den Dienst selbst; die Anwendung hat nur Leserechte.
2. **Datenbank-Trigger** auf allen buchungsrelevanten Tabellen (Rechnungen nach Freigabe, Belege nach Buchung, Zahlungen,
   Journal, Signaturen, DATEV-Exporte, Kündigungseingänge): UPDATE/DELETE werden abgewiesen.
3. **Anwendungslogik**: Korrekturen nur über Storno/Gegenbuchung; jede Aktion wird im Änderungsprotokoll (Audit-Log)
   mit Zeit, Person und Inhalt protokolliert (sensible Kennungen maskiert).

### 3.2 Nummernkreise
Rechnungsnummern und Buchungsnummern (`BU-JJJJ-NNNNNN`) vergibt der Festschreibungsdienst lückenlos in derselben
Transaktion wie die Festschreibung. Geht nach der Festschreibung die Verbindung verloren, übernimmt die Wiederholung die
bereits festgeschriebene Nummer (deterministische Vorgangs-ID) – es entstehen weder Lücken noch Doppelvergaben.

### 3.3 Festschreibung von Perioden
- Ein Monat wird **spätestens mit der Umsatzsteuer-Voranmeldung** festgeschrieben (Steuern → Voranmeldung → Sperren).
- Vorher erzeugt das System alle Regelbuchungen und prüft auf offene Punkte (ungebuchte Belege, nicht ausgestellte
  Rechnungen, nicht zugeordnete Bankumsätze, Belegdateien ohne Beleg). Offene Punkte blockieren; eine Festschreibung
  trotz offener Punkte ist nur mit ausdrücklicher Bestätigung möglich, die mit festgeschrieben wird.
- Nach der Sperre lehnen Festschreibungsdienst **und** Datenbank Buchungen in diesen Monat ab. Später bekannt gewordene
  Vorgänge werden mit ihrem **Originalbelegdatum** in die nächste offene Periode gebucht (Buchungstext-Vermerk).

### 3.4 Integritätsprüfung
Täglich 02:00 Uhr prüft die Automatisierung „Integritätsprüfung (GoBD)“ die gesamte Hash-Kette. Bei einer Abweichung:
Fehlerstatus, rote Aufgabe in der Übersicht, Alarm-E-Mail an alle Geschäftsführer. Vorgehen: Steuerberater informieren,
keine Änderungen vornehmen, letzte intakte Sicherung aufbewahren und vergleichen.

### 3.5 Datenzugriff der Finanzverwaltung (§ 147 Abs. 6 AO)
- Z1/Z2 (unmittelbarer/mittelbarer Zugriff): Leserechte für Prüfer über ein eigenes Google-Konto möglich.
- Z3 (Datenträgerüberlassung): **Buchhaltung → GoBD-Export (Z3)** erzeugt CSV-Dateien mit `index.xml` nach dem
  GDPdU-Beschreibungsstandard (Buchungsjournal, Kontenplan, Ausgangsrechnungen, Eingangsbelege, Zahlungen, Debitoren,
  Kreditoren) sowie ein Prüfprotokoll mit SHA-256 aller Dateien und dem Ergebnis der Hash-Ketten-Prüfung.

### 3.5a Dokumentenarchiv
Alle Belege, Rechnungen, Mahnungen, signierten Verträge, Angebote, Entgeltabrechnungen, SEPA-Dateien und versendeten E-Mails werden
automatisch archiviert: SHA-256 je Dokument, Kettenhash über alle Vorgänger, Inhalt und Frist per Trigger unveränderlich.
Vernichtung erst nach Fristablauf (Metadaten und Hash bleiben), Integritätsprüfung jederzeit über die Oberfläche.

### 3.6 Aufbewahrung (§ 147 AO, § 257 HGB)
| Unterlagen | Frist |
|---|---|
| Bücher, Journale, Jahresabschlüsse, Inventare, Verfahrensdokumentation | 10 Jahre |
| Buchungsbelege (Rechnungen, Belege) – seit 2025 | 8 Jahre |
| Handels- und Geschäftsbriefe | 6 Jahre |
Fristbeginn: Ende des Kalenderjahres der letzten Eintragung bzw. Entstehung. OpenEmployed löscht buchungsrelevante Daten
nie; Löschungen nach Fristablauf erfolgen nur manuell nach Rücksprache mit dem Steuerberater (Datenschutz beachten).

## 4. Betriebsdokumentation

### 4.1 Datensicherung
Täglich 02:30 Uhr vollständige Sicherung beider Datenbanken (`pg_dump`, geprüft mit `pg_restore --list`, SHA-256).
Aufbewahrung: 30 Tage täglich, 12 Monate monatlich, 10 Jahre jährlich. Zusätzlich Kopie außer Haus (Storage Box).
Wiederherstellung wird **mindestens halbjährlich** testweise durchgeführt und protokolliert (docs/betrieb.md).

### 4.2 Zugriffsschutz
Anmeldung ausschließlich über Google Workspace (Domain-Beschränkung, 2-Faktor über Google). Sitzungen serverseitig,
nur als Hash gespeichert, Ablauf nach Inaktivität. HTTPS über nginx (Let's Encrypt), CSP, HSTS. Container lauschen nur auf 127.0.0.1; Datenbank und
Festschreibungsdienst haben keine Ports.

### 4.2a Rollen und Mandanten
Rollen: Geschäftsführung (alles), Buchhaltung (Fachfunktionen ohne Verwaltung), Steuerberater (nur lesen und Exporte),
Mitarbeiter (Projekte, Zeiten, Belegfoto). Mehrere Firmen sind strikt getrennt (jede Abfrage nach Mandant gefiltert, eigene
Nummernkreise im Ledger); Zugriff auf weitere Firmen nur über ausdrückliche Freigabe mit eigener Rolle.

### 4.3 Änderungen an der Software
Änderungen werden versioniert (Git), vor dem Einsatz automatisiert getestet (über 250 Tests inkl. Unveränderbarkeit und
Nummernkreisen) und mit Datum in der Änderungshistorie unten vermerkt. Datenbankänderungen nur über nummerierte
Migrationen; Migrationen sind nicht umkehrbar (keine nachträgliche Veränderung festgeschriebener Daten).

## 5. Internes Kontrollsystem (IKS)
- Vier-Augen-Prinzip empfohlen für: Festschreibung einer Periode, Stornos über 1.000 €, Änderungen an Konten/BU-Schlüsseln.
- Monatlicher Abschluss bis zum 10. des Folgemonats: Belege vollständig, Bank abgestimmt, UStVA, Festschreibung, DATEV-Export.
- Monatsbericht per E-Mail (am 3.) und tägliche Liquiditätsprüfung.
- Die KI liest Belege nur aus; die Kontierung folgt den von den Geschäftsführern festgelegten Lieferanten-Regeln.
  Jede Buchung erfolgt per Klick; neue Lieferanten werden vor der ersten Buchung manuell einsortiert.

## Änderungshistorie
| Datum | Änderung | Freigabe |
|---|---|---|
| 14.11.2026 | Erstfassung | – |
| 15.11.2026 | Kontierung über Lieferanten-Regeln statt KI-Kontierung; Sammelbuchung per Klick; Prüfhinweise | – |
| 15.11.2026 | Anlagenbuchhaltung, Abschlussperiode JJJJ-13, Jahresabschluss-Vorbereitung, Bankabgleich Ausgaben, Zeiterfassung, Reisekosten, ELSTER-Übertragung, Handy-Erfassung | – |
| 15.11.2026 | Lohnbuchhaltung, SEPA, Eröffnungsbilanz/Import, Dokumentenarchiv, BWA, ZM, Vorauszahlungen, Monatsabschluss, Rollen, Mandanten, Fremdwährung, XRechnung/Peppol, Vertrieb, Workflows; Produktname OpenEmployed | – |
| 16.11.2026 | Rollen mit Rechten je Bereich (eigene Rollen), Stempeluhr mit automatischem Pausenabzug, Urlaub/Abwesenheiten mit Genehmigung und Team-Kalender | – |
| 16.11.2026 | Zeitkonto/Überstunden, On-/Offboarding, Personalakte; Archivspiegelung (S3 Object Lock, WebDAV, Festplatte) und Vollexport; Lohnsteuer nach amtlichem PAP, Lohnarten (Einmalzahlung, Sachbezug, Dienstwagen, Pfändung, Entgeltfortzahlung, Elternzeit, Mutterschutz), DEÜV-Meldungen, Beitragsnachweise, Lohnsteuerbescheinigung | – |
