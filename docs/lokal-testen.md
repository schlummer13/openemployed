# OpenEmployed lokal ausprobieren

Ohne Google-Anmeldung und ohne echten Mailversand – alles läuft auf dem eigenen Rechner.

## Starten

```bash
make up          # Stack inkl. lokalem Proxy (Caddy) und Test-Postfach (Mailpit)
make demo        # Demodaten anlegen (mehrfach ausführbar, legt nichts doppelt an)
```

| Was | Adresse |
|---|---|
| App (Geschäftsführer) | http://localhost → „Als Entwickler anmelden“ |
| Test-Postfach (alle Mails) | http://localhost:8025 |
| Kundenportal | http://localhost/portal |
| Kündigungsformular | http://localhost/kuendigen |

Die lokale KI (Ollama mit Qwen 2.5 VL) startet automatisch mit. Beim allerersten Start lädt sie das Modell
herunter (lokal `qwen2.5vl:3b`, ca. 3 GB) – Fortschritt unter Automatisierungen → KI für Belege.

## Was die Testeinstellungen bewirken (`.env`)

| Einstellung | Wirkung |
|---|---|
| `DEV_LOGIN_ENABLED=true` | Anmeldung ohne Google mit beliebiger E-Mail-Adresse |
| `MAIL_AUTH_MODE=local`, `SMTP_HOST=mailpit`, `SMTP_PORT=1025` | Jede Mail (Rechnung, Angebot, Willkommensmail, Mahnung, Anmeldecode, Berichte) landet in Mailpit – nichts verlässt den Rechner. Das Belegpostfach (Abruf per IMAP) ist im Testmodus aus. |

Beide Einstellungen sind in Produktion technisch gesperrt (`APP_ENV=production` startet damit nicht).

## Demodaten

- 4 Gesellschafter-Geschäftsführer mit Lohnsteuerklasse, Kinderfreibeträgen, Kirchensteuer, Krankenkasse
- 5 Kunden, 4 Produkte, 2 aktive Verträge, 1 Angebot, 4 Rechnungen (2 bezahlt, 2 offen)
- 6 Lieferanten mit Regel (Standardkonto), gebuchte Belege der letzten 6 Monate (ältere bezahlt), Entwürfe „bereit zum
  Buchen“ und ein neuer Lieferant (Adobe) zum Einsortieren
- 4 Budgetbereiche (Marketing ist diesen Monat bewusst über Budget), 3 Projekte mit Aufgaben, Kontostand

## Gut zum Ausprobieren

1. **Kundenportal:** http://localhost/portal → `praxis@lenz.test` → Code aus Mailpit eingeben.
2. **Rechnung schreiben:** Rechnungen → Neue Rechnung → freigeben → per Mail senden → in Mailpit ansehen (PDF + E-Rechnung).
3. **Belege einsortieren und buchen:** Unter Belege steht „Adobe“ als neuer Lieferant → „Einsortieren“ → Konto wählen
   (z. B. 6837 Lizenzen), „für diesen Lieferanten merken“ → der Beleg wandert zu „Bereit zum Buchen“ → „Belege buchen“.
   Künftige Adobe-Rechnungen werden automatisch so kontiert. Mit „+ Beleg“ → Datei → „Automatisch erkennen“ liest die
   lokale KI ein PDF oder Foto aus (auf einem Mac ohne GPU dauert das etwas).
4. **Budgets:** Ausgaben ohne Bereich zuordnen, Monatsbudget ändern.
5. **Schnellsuche:** ⌘K (Mac) bzw. Strg+K.

## Neu anfangen

Die lokale Datenbank enthält alles, was beim Testen angelegt wurde. Für einen frischen Stand (löscht **alle lokalen
Daten** unwiderruflich, auch Documenso-Testdaten) selbst ausführen:

```bash
docker compose --profile lokal down
docker volume rm openemployed_postgres-data
make up && sleep 30 && make demo
```

Festgeschriebene Belege lassen sich – wie in Produktion – nicht ändern oder löschen; das gilt auch im Test.
