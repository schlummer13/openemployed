# Mitarbeiter und Lohn
> Mitarbeiterstamm, monatlicher Lohnlauf, Lohnbuchung, Zahlungen, Lohnsteuer-Anmeldung und Mitarbeiter-Portal.

## Mitarbeiter anlegen
Unter **Mitarbeiter & Lohn** legt ihr Mitarbeiter mit allen Daten an: Person und Anschrift, Beschäftigungsart (sozialversicherungspflichtig,
Minijob, Werkstudent), Vergütung (Festgehalt oder Stundenlohn), Wochenstunden, Steuer-ID, Steuerklasse, Kinderfreibeträge,
Konfession, Krankenkasse mit Zusatzbeitrag oder private Versicherung, Kinder unter 25 und Bankverbindung. Geprüft werden
Mindestlohn und Minijob-Grenze. Gesellschafter-Geschäftsführer mit Gehalt (unter **GF-Gehälter**) kommen automatisch in den Lohnlauf.

Unter **Arbeitstage & Urlaub** wählt ihr die Wochentage, an denen die Person arbeitet (z. B. Mo–Mi bei Teilzeit), den
**Jahresurlaub als Gesamtzahl** und einen Resturlaub aus dem Vorjahr. Der **Urlaubsrechner** zeigt sofort, wie viele Tage im
Eintrittsjahr zustehen – mehr dazu im Kapitel *Stempeluhr und Urlaub*.

## Lohnlauf
1. Monat wählen, **Lohnlauf anlegen** – alle aktiven Mitarbeiter werden abgerechnet (bei Ein-/Austritt anteilig).
2. Bei Stundenlöhnen die tatsächlichen Stunden eintragen und **Neu berechnen**. Ohne Eingabe werden die Stunden aus der
   **Stempeluhr** übernommen (Arbeitszeit nach gesetzlicher Pause); die Abrechnung vermerkt das.
3. Jede Zeile zeigt Brutto, Steuern, SV-Anteile, Auszahlung und Arbeitgeberkosten; ein Klick zeigt alle Positionen, „PDF“ die Entgeltabrechnung.
4. **Freigeben & buchen** – die Abrechnungen werden unveränderlich, die Lohnbuchung wird festgeschrieben und die Abrechnungen
   erscheinen im Mitarbeiter-Portal.
5. Zahlungen: SEPA-Datei für die Nettolöhne erzeugen, danach Nettolöhne, Lohnsteuer (bis zum 10. des Folgemonats) und
   Sozialversicherung (drittletzter Bankarbeitstag) jeweils **als bezahlt buchen**.

## Lohnsteuer-Anmeldung
Nach der Freigabe stehen die Werte der Lohnsteuer-Anmeldung bereit (Kz 86, 42, 49, 61/62, 83). Mit eingerichtetem ERiC
direkt an ELSTER senden, sonst XML herunterladen oder die Werte in „Mein ELSTER“ übertragen.

## Mitarbeiter-Portal
Mit aktiviertem Portal-Zugang melden sich Mitarbeiter unter `/portal` mit ihrer E-Mail und einem Code an. Sie sehen ihre
Abrechnungen als PDF und pflegen Adresse, Telefon und Bankverbindung selbst. Steuermerkmale ändern sie nicht – die kommen vom Finanzamt.

## Entgeltbescheinigung
Jede Abrechnung ist eine **Entgeltbescheinigung nach § 108 Abs. 3 GewO und der Entgeltbescheinigungsverordnung (EBV)** und enthält:

- **Arbeitgeber:** Name und Anschrift. **Beschäftigte:** Name, Anschrift, Geburtsdatum, Personalnummer, Tätigkeit.
- **Versicherungsnummer**, **Eintritt** und ggf. **Austritt**, Art der Beschäftigung, **Abrechnungszeitraum**.
- **Steuer:** Steuer-ID, Steuerklasse, Kinderfreibeträge, Konfession (Kirchensteuermerkmal), Steuertage.
- **Sozialversicherung:** Krankenkasse, **Beitragsgruppenschlüssel** (KV RV AV PV, z. B. 1 1 1 1, Minijob 6 1 0 0),
  **Personengruppenschlüssel** (101 sozialversicherungspflichtig, 106 Werkstudent, 109 Minijob), SV-Tage.
- **Bezüge nach Art, Menge, Satz und Betrag** (z. B. 84 Std. × 16,50 €; Gehalt anteilig bei Ein-/Austritt), laufend/einmalig.
- **Steuerrechtliches Brutto** und **SV-Brutto** (RV/AV und KV/PV, bis zur Beitragsbemessungsgrenze bzw. im Übergangsbereich
  die beitragspflichtige Einnahme).
- Lohnsteuer, Solidaritätszuschlag und Kirchensteuer einzeln; KV-, RV-, AV- und PV-Beiträge des Arbeitnehmers einzeln.
- **Nettoentgelt**, Zuschüsse (z. B. zur privaten KV/PV) und **Auszahlungsbetrag** mit Ziel-IBAN (gekürzt).
- **Jahreswerte** (Januar bis Abrechnungsmonat), das **Urlaubskonto** und die Arbeitgeberanteile zur Information.

Die Angaben werden beim Lohnlauf mit der Abrechnung gespeichert, damit spätere Stammdatenänderungen eine freigegebene
Abrechnung nicht verändern.

## Midijob (Übergangsbereich)
Zwischen Minijob-Grenze (603 €) und 2.000 € gilt § 20a SGB IV: Die Beiträge werden aus einer reduzierten beitragspflichtigen
Einnahme berechnet, der Arbeitnehmeranteil zusätzlich aus einer eigenen, noch kleineren Bemessung (Faktor F 2026: 0,6619).
OpenEmployed berechnet beides automatisch; die Abrechnung zeigt beitragspflichtige Einnahme, AN-Bemessung und Faktor F.
Umlagen U1/U2/Insolvenzgeld werden vom tatsächlichen Entgelt berechnet.

## Lohnsteuer nach amtlichem Programmablaufplan
Lohnsteuer, Solidaritätszuschlag und Kirchensteuer werden nach dem **amtlichen Programmablaufplan (PAP)** des
Bundesfinanzministeriums berechnet – automatisch erzeugt aus der offiziellen Datei und centgenau gegen den BMF-Lohnsteuerrechner
geprüft (alle Steuerklassen, Faktorverfahren, Freibeträge, private Krankenversicherung, Einmalzahlungen). Kirchensteuer: 8 % in
Bayern und Baden-Württemberg, sonst 9 %. Freibetrag, Hinzurechnungsbetrag und Faktor (Steuerklasse IV) pflegt ihr beim Mitarbeiter.

## Lohnarten
Unter **Lohnabrechnung → Lohnpositionen** erfasst ihr je Monat:

- **Einmalzahlung** (Urlaubs-/Weihnachtsgeld, Bonus): Lohnsteuer nach dem Jahreslohnverfahren (§ 39b Abs. 3 EStG), Sozialversicherung
  bis zur anteiligen Jahres-Beitragsbemessungsgrenze.
- **Prämie/Zulage (laufend)** und **Überstunden** – Auszahlungen aus dem Zeitkonto erscheinen automatisch.
- **Sachbezug**: steuer- und beitragspflichtig, wird vom Netto wieder abgezogen. Gutscheine bis **50 € im Monat** bleiben steuerfrei
  (Haken „Gutschein bis 50 €“; die Freigrenze wird geprüft).
- **Steuerfreie Erstattung** (z. B. Auslagen) – wird mit ausgezahlt.
- **Vorschuss** und **Nettoabzug** – werden vom Auszahlungsbetrag abgezogen.

Automatisch berücksichtigt:

- **Dienstwagen** (Listenpreis, Antrieb, Entfernung beim Mitarbeiter): 1 % (Hybrid 0,5 %, Elektro 0,25 %) plus 0,03 % je km, abzüglich
  Zuzahlung.
- **Unbezahlter Urlaub, Elternzeit, Mutterschutz**: Gehalt anteilig nach Kalendertagen (30-Tage-Methode); im Mutterschutz der
  steuerfreie **Zuschuss zum Mutterschaftsgeld** (Nettoentgelt je Tag − 13 €).
- **Krankheit über 6 Wochen**: ab Tag 43 Krankengeld der Krankenkasse – kein Entgelt, SV-Tage entfallen, Unterbrechungsmeldung.
- **Pfändungen** (siehe unten).

## Pfändungen
Unter **Lohnabrechnung → Pfändungen** legt ihr Pfändungs- und Überweisungsbeschlüsse oder Abtretungen an: Gläubiger, Aktenzeichen,
IBAN, Forderung, Zustellungsdatum (Rangfolge) und Zahl der Unterhaltsberechtigten. Der pfändbare Betrag wird nach § 850c ZPO aus
dem Nettoeinkommen berechnet (die Hälfte der Überstundenvergütung bleibt unpfändbar); feste Beträge (z. B. Unterhalt nach § 850d)
sind möglich. Der Betrag wird einbehalten, auf der Abrechnung ausgewiesen und mit der SEPA-Datei der Gehälter an den Gläubiger
überwiesen. Die Pfändungsfreigrenzen ändern sich jährlich zum 1.7. – unter **Einstellungen** anpassbar.

## SV-Meldungen und Beitragsnachweise
Unter **Lohnabrechnung → SV-Meldungen & Beitragsnachweise** stehen alle fälligen **DEÜV-Meldungen** mit Inhalt und Frist:
Anmeldung (10), Abmeldung (30), Unterbrechung (51 Krankengeld/Mutterschaftsgeld, 52 Elternzeit), Ende wegen unbezahltem Urlaub über
einen Monat (34) und Jahresmeldung (50, bis 15.2.). Darunter je Krankenkasse bzw. Minijob-Zentrale der **Beitragsnachweis** des
Monats mit Fälligkeit (drittletzter Bankarbeitstag) und Übermittlungsfrist (zwei Arbeitstage vorher). Übermittelt wird über das
kostenlose SV-Meldeportal oder euer Lohnbüro; danach markiert ihr die Meldung als übermittelt. Fällige oder überfällige Meldungen
erscheinen unter „Zu erledigen“. Pflicht: **Betriebsnummer** unter Einstellungen, beim Mitarbeiter SV-Nummer, Krankenkasse und
Tätigkeitsschlüssel.

## Lohnsteuerbescheinigung
Nach Jahresende erstellt **Lohnsteuerbescheinigungen → Aus Lohnläufen erstellen** je Mitarbeiter die Werte der
Lohnsteuerbescheinigung (Zeile 1 Dauer, 2 U-Zeiten, 3 Brutto, 4–6 Steuern, 15 Zuschuss Mutterschaftsgeld, 22a/23a Rentenversicherung,
24a PKV-Zuschuss, 25–27 Kranken-, Pflege-, Arbeitslosenversicherung, 28 private Basis-KV) und den Ausdruck als PDF für die
Mitarbeiter. Die elektronische Übermittlung (bis 28.2.) erfolgt über ELSTER/ERiC bzw. das Lohnbüro.

## Lohnbuchung
Mit der Freigabe wird gebucht: Bruttolöhne an Verbindlichkeiten Lohn/Lohnsteuer/Sozialversicherung; Sachbezüge gegen „Verrechnete
sonstige Sachbezüge“ (USt auf Kfz-Gestellung mit dem Steuerberater klären), steuerfreie Zahlungen als freiwillige soziale
Aufwendungen, Vorschüsse gegen „Forderungen gegen Personal“, Pfändungen als sonstige Verbindlichkeit – mit der Gehaltszahlung
wieder ausgeglichen.

## Grenzen
DEÜV-Meldungen, Beitragsnachweise und die elektronische Lohnsteuerbescheinigung brauchen für die direkte Übermittlung eine
zertifizierte Schnittstelle (ITSG bzw. ELSTER-Hersteller-ID). Bis dahin bereitet OpenEmployed alle Inhalte vor und ihr übermittelt
über das SV-Meldeportal/ELSTER oder das Lohnbüro. ELStAM werden nicht automatisch abgerufen – Steuerklasse, Freibeträge und Faktor
aus der ELStAM-Liste übernehmen. Kurzarbeit, Altersteilzeit, betriebliche Altersversorgung und Märzklausel-Fälle mit dem Lohnbüro
abstimmen. Gleicht vor dem Produktivstart einen Monat parallel ab.
