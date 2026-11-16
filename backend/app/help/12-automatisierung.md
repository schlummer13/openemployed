# Automatisierungen und Workflows
> Eingebaute Jobs und eigene Workflows im Baukasten: Auslöser, Blöcke, Platzhalter, Wenn/Sonst, HTTP, Python und Fehlersuche.

## Eingebaute Automatisierungen
Unter **Automatisierungen → Eingebaut** laufen Hintergrundjobs: Vertragsabrechnung, Mahnlauf, Belegpostfach, Zahlungszuordnung,
Buchungsjournal, Integritätsprüfung, Liquiditätswächter, Monatsbericht, Wochenüberblick, Archiv, Webhooks, EZB-Kurse und eure
Workflows. Jeder Job lässt sich ein- und ausschalten und sofort ausführen; die letzten Läufe sind protokolliert. Gebucht oder
festgeschrieben wird nie automatisch.

## Eigene Workflows
Unter **Automatisierungen → Eigene Workflows** baut ihr eigene Abläufe: ein **Auslöser** oben, darunter eine Kette von **Blöcken**,
die von oben nach unten ausgeführt werden. Vorlagen helfen beim Einstieg. **Speichern & testen** führt den Workflow sofort mit
Testdaten aus und zeigt das Ergebnis jedes Blocks direkt darunter.

## Auslöser
- **Ereignis:** startet, wenn etwas im System passiert – z. B. *Rechnung ausgestellt*, *Zahlung erfasst*, *Beleg gebucht*,
  *Angebot angenommen*, *Kunde angelegt*, *Lead verschoben*, *Lohnlauf freigegeben*, *Monat festgeschrieben*.
- **Zeitplan (Cron):** fünf Felder *Minute Stunde Tag Monat Wochentag* in deutscher Zeit. Beispiele:
  `0 7 * * *` täglich 07:00 · `0 8 * * 1` montags 08:00 · `0 9 1 * *` am 1. des Monats · `*/15 * * * *` alle 15 Minuten.
  Wochentag 0 oder 7 = Sonntag. Jeder Termin läuft höchstens einmal.
- **Manuell:** nur über „Speichern & testen“ bzw. die API.

## Platzhalter
In allen Textfeldern lassen sich Werte mit `{{…}}` einsetzen. Das Feld mit dem Pfeil öffnet eine **durchsuchbare Liste** aller
verfügbaren Felder – gelernt aus echten Ereignissen eures Systems, ergänzt um Grundfelder.
- `{{data.number}}`, `{{data.total_gross}}`, `{{data.customer_name}}` – Daten des Ereignisses
- `{{steps.<kennung>.…}}` – Ergebnis eines vorherigen Blocks, z. B. `{{steps.pruefen.result.betrag}}` oder `{{steps.abfrage.body.id}}`
- `{{now}}` – Zeitpunkt der Ausführung

Besteht ein Feld nur aus einem Platzhalter, bleibt der Typ erhalten (Zahl, Liste, Objekt).

## Blöcke
- **Wenn / Sonst:** Abzweigung. Die Linie teilt sich in einen grünen Pfad **Ja** und einen roten Pfad **Nein**; jeder Pfad hat
  eine eigene Blockkette, danach geht es gemeinsam weiter. Bis zu drei Ebenen verschachtelbar.
- **Bedingung:** beendet den Lauf, wenn sie nicht erfüllt ist (Status „Bedingung nicht erfüllt“).
- **E-Mail senden:** Empfänger (mehrere mit Komma), Betreff, Text – über euer eingerichtetes Postfach, archiviert als .eml.
- **Aufgabe anlegen:** in einem Projekt, mit Fälligkeit in Tagen.
- **HTTP-Anfrage:** GET/POST/PUT/PATCH/DELETE mit **Kopfzeilen** (z. B. `Authorization: Bearer …`, `Content-Type`, `X-API-Key`)
  und Body (JSON oder Text). Die Antwort steht unter `{{steps.<kennung>.status}}` und `{{steps.<kennung>.body…}}` bereit.
  Interne Adressen sind gesperrt.
- **Python-Code:** eigener Code in einer abgeschotteten Sandbox, siehe unten.

Vergleiche: ist gleich, ist ungleich, größer als, mindestens, kleiner als, höchstens, enthält, enthält nicht, ist leer, ist nicht leer.
Zahlen werden als Zahlen verglichen (Komma oder Punkt), sonst als Text.

## Python-Schritte
Der Code bekommt `data` (mit `data["data"]` = Ereignisdaten und `data["steps"]` = bisherige Ergebnisse) und schreibt sein
Ergebnis in `result`. Erlaubt sind u. a. `requests`, `re`, `json`, `math`, `datetime`, `decimal`, `statistics`, `hashlib`, `base64`.

```python
import requests, re
r = requests.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml", timeout=8)
kurs = float(re.search(r"currency='USD' rate='([0-9.]+)'", r.text).group(1))
betrag = float(data["data"].get("total_gross") or 0)
result = {"usd": round(betrag * kurs, 2), "auffaellig": betrag > 10000}
```

Grenzen der Sandbox: 5 Sekunden CPU, 20 Sekunden Laufzeit, 256 MB Speicher, keine Dateien schreiben, keine Prozesse starten.
`requests` erreicht nur öffentliche Adressen – Datenbank, OpenEmployed selbst, localhost und interne Netze sind gesperrt.

## Beispiele
**Große Rechnung an die Geschäftsführung, sonst an die Buchhaltung**
Auslöser *Rechnung ausgestellt* → Wenn/Sonst `{{data.total_gross}}` mindestens `5000` → Ja: E-Mail an gf@… · Nein: E-Mail an buchhaltung@….

**Angebot angenommen → Kickoff-Aufgabe**
Auslöser *Angebot angenommen* → Aufgabe „Kickoff {{data.number}} – {{data.customer_name}}“ im Projekt *Aufträge*, fällig in 2 Tagen.

**Montags Kennzahlen nach Slack**
Zeitplan `0 8 * * 1` → HTTP POST an den Slack-Webhook mit Body `{"text": "Neue Woche – offene Posten prüfen"}`.

**Neuer Kunde ins CRM eines anderen Tools**
Auslöser *Kunde angelegt* → HTTP POST mit Kopfzeile `Authorization: Bearer <token>` und Body
`{"name": "{{data.company_name}}", "email": "{{data.email}}"}`.

## Fehlersuche
- Unter **Letzte Läufe** steht jeder Lauf mit Status und Fehlermeldung; im Editor zeigt der Testlauf das Ergebnis je Block.
- „Bedingung nicht erfüllt“ ist kein Fehler – der Lauf hat planmäßig gestoppt.
- Leere Platzhalter: Feld in der Auswahlliste prüfen; Ereignisdaten unterscheiden sich je Auslöser.
- Workflows laufen über den Job „Eigene Workflows“ – ist er ausgeschaltet, laufen keine Ereignis- und Zeitplan-Workflows.
