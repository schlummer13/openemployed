# Betrieb – Installation, Updates, Datensicherung, KI-Hardware

## 1. Server (Netcup)

Empfehlung: Netcup Root-Server (z. B. RS 2000: 8 vCPU, 16 GB RAM, 512 GB SSD) mit Debian 12/13.

```bash
apt update && apt install -y docker.io docker-compose-plugin git ufw
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable
git clone <repo> /opt/selbstlite && cd /opt/selbstlite
scripts/generate-env.sh          # erzeugt .env mit Zufallspasswörtern
```

In der `.env` setzen:

| Variable | Wert |
|---|---|
| `APP_ENV` | `production` |
| `APP_ORIGIN` | `https://app.<domain>.de` |
| `PORTAL_ORIGIN` | `https://portal.<domain>.de` |
| `DOCUMENSO_PUBLIC_URL` | `https://sign.<domain>.de` |
| Ports (nur 127.0.0.1) | `FRONTEND_PORT=8310`, `BACKEND_PORT=8311`, `DOCUMENSO_PORT=8312` |
| `DEV_LOGIN_ENABLED` | `false` (in Produktion technisch verboten) |
| Google-Login | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_ALLOWED_DOMAIN` |
| E-Mail | `MAIL_MAILBOX` + Dienstkonto (`secrets/google-mail-service-account.json`) |
| Versionen | `DOCUMENSO_IMAGE` auf eine feste Version pinnen (`OLLAMA_IMAGE` ist bereits gepinnt) |
| KI-Modell | `OLLAMA_MODEL` passend zum RAM des Servers (siehe Abschnitt 4) |

DNS: A-/AAAA-Records für `app`, `portal`, `sign` auf den Server.

**Reverse Proxy: nginx auf dem Host** (Caddy läuft in Produktion nicht – es gehört zum Compose-Profil `lokal`).
Die Container veröffentlichen ihre Ports nur auf `127.0.0.1`; nginx terminiert TLS und leitet weiter:

| Domain | Pfad | Ziel |
|---|---|---|
| `app.<domain>.de` | `/api/*` | `127.0.0.1:8311` (Backend) |
| `app.<domain>.de` | alles andere | `127.0.0.1:8310` (Frontend) |
| `portal.<domain>.de` | `/api/portal/*`, `/api/public/*` | `127.0.0.1:8311` |
| `portal.<domain>.de` | `/portal`, `/kuendigen`, `/_next/*` | `127.0.0.1:8310` – alles andere → Weiterleitung auf `/portal` |
| `sign.<domain>.de` | alles | `127.0.0.1:8312` (Documenso) |

```bash
sudo cp infra/nginx/selbstlite.conf /etc/nginx/sites-available/
sudo cp infra/nginx/selbstlite-headers.conf infra/nginx/selbstlite-proxy.conf /etc/nginx/snippets/
sudo sed -i 's/example.de/<domain>.de/g' /etc/nginx/sites-available/selbstlite.conf
sudo ln -s /etc/nginx/sites-available/selbstlite.conf /etc/nginx/sites-enabled/
sudo certbot certonly --nginx -d app.<domain>.de -d portal.<domain>.de -d sign.<domain>.de
sudo nginx -t && sudo systemctl reload nginx
```

Wichtig: nginx muss `X-Forwarded-For` mit `$remote_addr` **überschreiben** (so in `selbstlite-proxy.conf`), sonst
könnten Clients die IP für Login-Sperren fälschen. Die Firewall (ufw) lässt nur 22, 80 und 443 zu.

```bash
docker compose up -d --build
docker compose ps                 # alle Dienste „healthy“
```

## 2. Updates

```bash
cd /opt/selbstlite
make update        # Sicherung → git pull → Neubau → Migrationen (scripts/update.sh, bricht bei Fehlern ab)
```

Danach: Übersicht öffnen, Automatisierungen → „Integritätsprüfung (GoBD)“ → Jetzt ausführen → muss „intakt“ melden.
Jede Änderung in der Änderungshistorie der Verfahrensdokumentation vermerken.

## 3. Datensicherung und Wiederherstellung

- Täglich 02:30 Uhr automatisch (Dienst `backup`), Ablage unter `BACKUP_PATH` (Standard `./backups`).
- Status, Liste und Download: *System & Integrationen → Version & Sicherung* (dort auch „Jetzt sichern“); sonst Datei `backups/last_success`.
- **Außer-Haus-Kopie** (Pflicht – ein Serverausfall darf keine Daten kosten), z. B. Netcup Storage Box:

```bash
# Crontab des Servers: täglich 04:00 Uhr verschlüsselt kopieren
0 4 * * * rsync -a --delete /opt/selbstlite/backups/ u123456@u123456.your-storagebox.de:selbstlite/
```

**Wiederherstellungstest (halbjährlich, protokollieren):**

```bash
scripts/restore.sh backups/daily/selbstlite_<datum>.dump selbstlite_restore_test
```

Produktive Wiederherstellung (nur nach Rücksprache): Dienste stoppen, Datenbank `selbstlite` umbenennen, Sicherung als
`selbstlite` einspielen, Dienste starten, Integritätsprüfung ausführen.

## 4a. E-Mail-Anbieter

selbstlite versendet über SMTP und holt Belege per IMAP ab – mit jedem Anbieter. `MAIL_PROVIDER` wählt die Voreinstellung
(Server, Ports, Verschlüsselung), `SMTP_HOST`/`IMAP_HOST` überschreiben sie. Status und Testversand: Einstellungen → E-Mail
bzw. `POST /api/mail/test` und `POST /api/mail/test-inbox` (prüft das Postfach, ohne etwas zu verändern).

| Anbieter | `MAIL_PROVIDER` | `MAIL_AUTH_MODE` | Hinweis |
|---|---|---|---|
| Google Workspace | `google` | `service_account` | Dienstkonto mit domänenweiter Delegation, Bereich `https://mail.google.com/` |
| Gmail / Workspace ohne Dienstkonto | `google` | `app_password` | App-Passwort, setzt 2-Schritt-Bestätigung voraus |
| Microsoft 365 / Exchange Online | `microsoft` | `oauth_client` | App-Registrierung, siehe unten (Passwort-SMTP ist bei Microsoft abgeschaltet) |
| IONOS, STRATO, Hetzner, mailbox.org, Posteo, GMX, WEB.DE, Telekom, iCloud, Fastmail, Zoho | `ionos`, `strato`, `hetzner`, `mailbox_org`, `posteo`, `gmx`, `webde`, `telekom`, `icloud`, `fastmail`, `zoho` | `app_password` | ggf. IMAP/SMTP im Webmail aktivieren bzw. App-Passwort anlegen |
| netcup-Webhosting, ALL-INKL | `netcup`, `allinkl` | `app_password` | `SMTP_HOST`/`IMAP_HOST` aus dem Kundenmenü (z. B. `mxe8xx.netcup.net`) |
| Eigener Mailserver | `smtp` | `app_password` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `IMAP_HOST` setzen |

Weicht der Anmeldename von der Adresse ab, `MAIL_USERNAME` setzen. Belegeingang: Bei Google bekommen verarbeitete Mails das
Label `selbstlite/verarbeitet`; bei allen anderen werden sie in den Ordner `MAIL_PROCESSED_FOLDER` verschoben (nie gelöscht).
Abgeholt werden Mails mit Anhang der letzten 60 Tage aus `MAIL_IMAP_FOLDER` – am besten Rechnungen per Postfachregel in einen
eigenen Ordner sortieren oder eine eigene Adresse (z. B. belege@…) als `MAIL_IMAP_MAILBOX` nutzen.

### Microsoft 365 einrichten (einmalig, als Exchange-Administrator)

1. **Entra ID → App-Registrierungen → Neue Registrierung** (Name z. B. „selbstlite“, nur dieses Verzeichnis).
   Verzeichnis-ID → `MS_TENANT_ID`, Anwendungs-ID → `MS_CLIENT_ID`.
2. **Zertifikate & Geheimnisse → Neuer geheimer Clientschlüssel** → Wert als `MS_CLIENT_SECRET` (Ablaufdatum notieren!).
3. **API-Berechtigungen → Berechtigung hinzufügen → Von meiner Organisation verwendete APIs → „Office 365 Exchange Online“ →
   Anwendungsberechtigungen**: `SMTP.SendAsApp` und `IMAP.AccessAsApp` → **Administratorzustimmung erteilen**.
4. Exchange-Online-PowerShell (Objekt-ID der *Unternehmensanwendung*, nicht der App-Registrierung):
   ```powershell
   Connect-ExchangeOnline
   New-ServicePrincipal -AppId <MS_CLIENT_ID> -ObjectId <Objekt-ID der Unternehmensanwendung>
   Add-MailboxPermission -Identity rechnungen@firma.de -User <Objekt-ID> -AccessRights FullAccess
   Add-RecipientPermission -Identity rechnungen@firma.de -Trustee <Objekt-ID> -AccessRights SendAs
   Set-CASMailbox -Identity rechnungen@firma.de -SmtpClientAuthenticationDisabled $false
   ```
5. `.env`: `MAIL_PROVIDER=microsoft`, `MAIL_AUTH_MODE=oauth_client`, `MAIL_MAILBOX=rechnungen@firma.de`, die drei `MS_*`-Werte;
   `docker compose up -d backend`, dann Testmail senden. Fehler wie `AADSTS7000215` (falsches Geheimnis) oder
   `535 5.7.3` (Postfachberechtigung/SMTP AUTH fehlt) erscheinen im Klartext.

Signatur-Mails (Documenso) laufen über eigenes SMTP (`DOCUMENSO_SMTP_*`); Documenso kann kein OAuth – bei Microsoft 365
ein SMTP-Relay (z. B. Brevo, Mailjet) oder ein separates Postfach eines anderen Anbieters verwenden.

## 4. Lokale KI (empfohlen statt Cloud-KI)

Die Belegerkennung und Kontierung läuft mit einem offenen Vision-Sprachmodell auf eigener Hardware (Ollama).
Kosten je Beleg: 0 €. Keine Belegdaten verlassen das Unternehmen (kein Auftragsverarbeitungsvertrag nötig).

| Hardware | Modell | Dauer je Beleg (ca.) | Hinweis |
|---|---|---|---|
| Mac mini M4 / M4 Pro, 24 GB | `qwen2.5vl:7b` | 5–15 s | leise, ~10 W, im Büro per VPN/Tailscale erreichbar |
| PC mit NVIDIA RTX 3060 12 GB / 4060 Ti 16 GB | `qwen2.5vl:7b` | 3–8 s | `deploy`-Abschnitt in docker-compose einkommentieren |
| Netcup-Server ohne GPU (8 vCPU) | `qwen2.5vl:3b` | 1–3 min | funktioniert, aber langsam – für wenige Belege ausreichend |

**Eingebaut:** Der Dienst `ollama` startet mit `docker compose up -d` automatisch und lädt das Modell aus
`OLLAMA_MODEL` beim ersten Start selbst herunter (einmalig, 3–6 GB, liegt danach im Volume `ollama-data`).
Bis der Download fertig ist, zeigt **Automatisierungen → KI für Belege** „lädt“; danach „bereit“.

| `.env` | Wirkung |
|---|---|
| `OLLAMA_MODEL=qwen2.5vl:7b` | Standard, beste Qualität – Server mit ≥ 12 GB RAM für die KI |
| `OLLAMA_MODEL=qwen2.5vl:3b` | kleiner, für Server/Rechner mit 8 GB RAM |
| `OLLAMA_URL=http://<mac-mini>:11434` | stattdessen KI auf separater Hardware nutzen (eingebauter Dienst bleibt ungenutzt) |
| `AI_PROVIDER=none` | KI abschalten (Belege werden regelbasiert gebucht) |

Modellwechsel: `OLLAMA_MODEL` ändern und `docker compose up -d ollama backend` – das neue Modell wird automatisch
geladen. Download-Fortschritt: `docker compose logs -f ollama`. E-Rechnungen (XRechnung/ZUGFeRD) werden immer ohne KI gelesen.

## 5. ELSTER (Umsatzsteuer-Voranmeldung direkt übermitteln)

selbstlite erzeugt den ELSTER-Datensatz aus den festgeschriebenen Kennzahlen. Übermittelt wird ausschließlich über
**ERiC**, die offizielle Bibliothek der Finanzverwaltung (Plausibilitätsprüfung, Verschlüsselung, Versand):

1. Kostenlos im ELSTER-Entwicklerbereich registrieren → **Hersteller-ID** (`ELSTER_HERSTELLER_ID` in der `.env`).
2. ERiC für Linux herunterladen und das Verzeichnis nach `secrets/eric` kopieren (enthält `lib/libericapi.so`).
3. Organisationszertifikat aus „Mein ELSTER“ als `secrets/elster.pfx` ablegen.
4. `COMPANY_STATE` (Bundesland des Firmensitzes) prüfen – daraus wird die 13-stellige ELSTER-Steuernummer gebildet.
5. Steuern → Voranmeldung → **Prüfen** (ERiC-Plausibilitätsprüfung), dann **Senden** im Testmodus
   (`ELSTER_TEST_MODE=true`, Testmerker – das Finanzamt verarbeitet den Fall nicht).
6. Erst nach erfolgreicher Testübertragung `ELSTER_TEST_MODE=false` setzen.

Gesendet werden nur festgeschriebene Zeiträume. Die Zertifikats-PIN wird bei jedem Versand eingegeben und nicht
gespeichert. Jede Prüfung/Übertragung wird mit Transferticket protokolliert. Ohne ERiC: Datensatz (XML) herunterladen
bzw. Kennzahlen in „Mein ELSTER“ übertragen.

## 6. Handy-App

Die Oberfläche ist als App installierbar (Browser-Menü → „Zum Startbildschirm hinzufügen“). Sie startet in
**Beleg erfassen**: Foto aufnehmen, die lokale KI liest den Beleg aus, bekannte Lieferanten werden nach ihrer Regel
kontiert. Es werden keine Finanzdaten offline auf dem Gerät gespeichert.

## 7. Überwachung

- Automatisierungen-Seite: letzter Lauf, Status und Verlauf aller Jobs.
- Integritätsprüfung täglich mit E-Mail-Alarm.
- `docker compose logs -f backend` für Fehleranalyse; Healthchecks für Datenbank, Ledger, Backend.

## 6. Notfall: Hash-Kette gebrochen

1. Nichts ändern, keine Belege buchen.
2. Steuerberater informieren.
3. Letzte Sicherung mit intakter Kette ermitteln (Wiederherstellung in Testdatenbank, Integritätsprüfung).
4. Ursache dokumentieren (Verfahrensdokumentation, Abschnitt Änderungshistorie).

## 8. Anmeldung, Rollen und Benutzer

- Verfahren (kombinierbar, in der App unter *System & Integrationen → Einstellungen → Anmeldung*): Google, Microsoft Entra ID,
  beliebiger OIDC-Anbieter, E-Mail + Passwort (scrypt) mit TOTP-Zwei-Faktor (optional verpflichtend).
- Weiterleitungs-URIs: `https://app.<domain>/api/auth/microsoft/callback` bzw. `/api/auth/oidc/callback`.
- „Rolle neuer SSO-Benutzer“: `none` = nur eingeladene Personen; der allererste Benutzer wird immer Geschäftsführung.
- Benutzer einladen unter *Benutzer & Rollen*; die Steuerkanzlei als „Steuerberater (nur lesen)“ – DATEV-/GoBD-Export inklusive.
- Passwort-Anmeldung: nach 5 Fehlversuchen 15 Minuten Sperre; nginx begrenzt `/api/auth/` zusätzlich je IP.

## 9. E-Rechnung: XRechnung und Peppol

- Kunde → E-Rechnungsformat „XRechnung 3.0“ + Leitweg-ID (Prüfziffer wird geprüft). Firmenprofil braucht Telefon und E-Mail (BR-DE).
- Versand per E-Mail (XRechnung als Hauptanhang, PDF als Sichtbeleg) oder Upload in ZRE/OZG-RE/Landesportale (*XRechnung*-Download).
- Peppol: Vertrag mit einem zertifizierten Access Point; dessen API-URL und Schlüssel sowie die eigene Peppol-ID unter
  *System & Integrationen → E-Rechnung (Peppol)* eintragen. OpenEmployed übergibt die XML per HTTPS (Header `X-Peppol-Receiver`).

## 10. Auslandskunden und Fremdwährungen

- Kunde mit Land, USt-IdNr. (Länderpräfix) und Rechnungswährung anlegen; Steuerfälle „EU-Leistung (Reverse Charge)“,
  „ig. Lieferung“, „Drittland“ werden gegen Land/USt-IdNr. geprüft und erscheinen in UStVA (Kz 21/41/45) und ZM.
- EZB-Kurse holt der Job „EZB-Wechselkurse“ werktags um 17 Uhr (nur bei Fremdwährungskunden); manuell unter *System → Währungen*.

## 11. Workflows, API und Webhooks

- Eigene Workflows unter *Automatisierungen → Eigene Workflows*; der Takt läuft minütlich.
- Python-Schritte laufen im Dienst `sandbox`: schreibgeschützt, ohne Rechte, 5 s CPU / 20 s / 256 MB je Schritt. Internetzugang
  (Netz `sandbox-egress`) nur zu öffentlichen Adressen – interne Netze, localhost und Cloud-Metadaten blockiert ein nicht
  abschaltbarer Audit-Hook. Wer gar keinen Internetzugang möchte: `sandbox-egress` beim Dienst `sandbox` entfernen.
- API-Schlüssel (*System & Integrationen*): `Authorization: Bearer sl_…`, Rechte „nur lesen“ oder „lesen und schreiben“.
- Webhooks signieren mit `X-OpenEmployed-Signature: t=<Zeit>,v1=<HMAC-SHA256(secret, "t.body")>`; Ziele im internen Netz sind gesperrt.

## 12. Mehrere Firmen

- Firmenauswahl oben in der Seitenleiste → „+ Neue Firma“ (Geschäftsführung). Jede Firma hat eigene Buchhaltung, Nummernkreise,
  Kunden, Belege, Lohn und Automatisierungen; gemeinsam sind Benutzerkonten, Anmeldeverfahren und Systemeinstellungen (Postfach, KI).
- Zugriff auf weitere Firmen vergibt die Geschäftsführung der jeweiligen Firma (eigene Rolle je Firma).
- Kunden- und Mitarbeiterportal gelten für die Stammfirma der Installation.

## 13. Stempeluhr und Urlaub

- Stempeluhr: Menü **Stempeluhr** (alle Benutzer mit Mitarbeiterprofil gleicher E-Mail) bzw. Mitarbeiter-Portal → Tab „Stempeluhr“.
  Pausen nach ArbZG/JArbSchG werden automatisch abgezogen; Korrekturen unter **Team-Kalender → Stempelzeiten** (mit Begründung).
- Urlaub: **Urlaub & Abwesenheit** bzw. Portal → „Urlaub“. Genehmigung im **Team-Kalender** durch Rollen mit „Lohn & Personal –
  bearbeiten“; Benachrichtigungen gehen über den konfigurierten Mailversand an die Geschäftsführung bzw. die Person.
- Je Mitarbeiter pflegen: Arbeitstage (Wochentage), Jahresurlaub als Gesamtzahl, Resturlaub Vorjahr, Bundesland (Feiertage).
- Lohnlauf: Stundenlöhner ohne manuell eingetragene Stunden erhalten die gestempelte Netto-Arbeitszeit des Monats.

## 14. Archiv-Spiegelung und Festplatte

- Archiv → **Spiegelung & Export**: Ziele S3 (mit Object Lock = WORM), WebDAV oder Ordner. Täglicher Lauf 02:00 (Automatisierung
  „Archiv prüfen und spiegeln“), Fehler rot unter „Zu erledigen“.
- S3 mit Object Lock: Bucket beim Anlegen mit Object Lock erstellen (z. B. Hetzner/IONOS/AWS in der EU), Zugangsschlüssel nur mit
  Schreib- und Leserecht auf diesen Bucket. Nur https-Endpunkte (Ausnahme lokale Testserver über `ARCHIVE_ALLOW_HTTP_HOSTS`).
- Festplatte/NAS: auf dem Server einhängen (z. B. `/mnt/usb-archiv`), in der `.env` `ARCHIVE_EXPORT_PATH=/mnt/usb-archiv`, dann
  `make update`. Ziel „Ordner“ anlegen; Dateien werden schreibgeschützt abgelegt.
- Vollexport: Archiv → Spiegelung & Export → „ZIP herunterladen“; Prüfung offline mit `python3 pruefen.py`.

## 15. Lohn: jährliche Pflege

- Neuer **Programmablaufplan** (meist November): XML von bmf-steuerrechner.de nach `backend/app/domain/pap/` legen,
  `python tools/pap_codegen.py app/domain/pap/Lohnsteuer2027.xml > app/domain/pap/lst2027.py`, in `app/domain/lohnsteuer.py`
  registrieren, Testfälle gegen die BMF-Prüfschnittstelle erzeugen (`tests/data/pap2027_bmf.json`).
- Beitragssätze und Beitragsbemessungsgrenzen in `app/domain/payroll.py` (`PayrollParams`), Minijob-/Midijob-Grenzen,
  Pfändungsfreigrenzen (jährlich 1.7., Lohn → Einstellungen).

## 16. Documenso (digitale Signatur)

- Verbindet sich beim Start automatisch (Konto der Geschäftsführung, API-Token, Webhook). Voraussetzungen in der `.env`:
  `DOCUMENSO_DB_PASSWORD`, `DOCUMENSO_PUBLIC_URL`, `DOCUMENSO_WEBHOOK_SECRET`; optional `DOCUMENSO_ADMIN_EMAIL`.
  `DOCUMENSO_API_TOKEN` leer lassen (nur für einen extern betriebenen Documenso setzen).
- Image ist auf `documenso/documenso:v2.21.1` festgelegt – die Einrichtung schreibt in Documensos Datenbank (Token, Webhook).
  Vor einem Documenso-Update die Einrichtung im Testsystem prüfen (System & Integrationen → Verbindung prüfen).
- Webhooks an das interne Backend sind über `NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS=backend` freigegeben.
- Status/Neuverbinden/Passwort-Link: System & Integrationen → Einstellungen → Digitale Signatur. Abgleich alle 15 Minuten
  (Automatisierung „Documenso-Abgleich“).


## 17. Geschwindigkeit und Stabilität

**Gemessen (lokal, angemeldeter Browser, über Caddy):**

| Anfrage | vorher p50 | jetzt p50 / p95 | davon im Server |
|---|---|---|---|
| Dashboard | 105 ms | 4,0 / 5,7 ms | 1,6 ms |
| „Zu erledigen“ | 27 ms | 3,2 / 5,0 ms | 1,6 ms |
| Hilfe-Suche | 27 ms | 3,1 / 3,7 ms | 1,3 ms |
| Rechnungen | – | 4,0 / 4,9 ms | 2,1 ms |
| Feiertage | – | 4,8 / 6,2 ms | 2,5 ms |

Last: 500 Anfragen, 50 gleichzeitig: 0 Fehler, ~560 Anfragen/s, p50 67 ms, p95 202 ms. Die Wartezeit bei Last entsteht
überwiegend in der Warteschlange des Browsers (max. 6 Verbindungen je Adresse), nicht im Server.

Ehrlich: Eine Antwortzeit von 1 ms über das Netz und 100 % Verfügbarkeit kann keine Software garantieren. Netzwerk, TLS und
der Browser kosten allein mehrere Millisekunden, und Hardware, Strom oder Internet können ausfallen. Die Maßnahmen zielen
darauf, dass die Software selbst nie der Engpass ist und sich nach Störungen von allein erholt.

**Was eingebaut ist:**

- **Lese-Zwischenspeicher** für Dashboard und „Zu erledigen“ (`app/core/read_cache.py`). Jede Änderung schreibt ins
  Audit-Protokoll. Ändert sich dessen höchste ID, ist der Speicher sofort ungültig, auch über mehrere Prozesse. Zusätzlich
  verfällt jeder Eintrag nach 30 s. Nach einer Änderung dauert der erste Aufruf so lange wie früher.
- Aufwendige Hintergrundarbeiten (SV-Meldungen prüfen) laufen stündlich als Automatisierung statt bei jedem Seitenaufruf.
- **Mehrere Server-Prozesse:** `WEB_CONCURRENCY` (Standard 2; etwa 1 je CPU-Kern). Die Hintergrundjobs sind per
  Datenbanksperre gegen doppelte Ausführung geschützt.
- **Server-Timing:** Jede Antwort trägt den Header `Server-Timing: app;dur=…`, sichtbar in den Entwicklertools des Browsers.
  Anfragen über 500 ms landen als Warnung im Log (`docker compose logs backend | grep "langsame Anfrage"`). Postgres
  protokolliert Abfragen über 500 ms.
- **Schutz vor Hängern:** Für Datenbankabfragen gilt eine Obergrenze von 60 s, Sperren werden nach 15 s abgebrochen,
  vergessene Transaktionen nach 120 s. Eine freie Verbindung wird höchstens 10 s lang gesucht, danach kommt eine klare
  Fehlermeldung statt endlosen Wartens.
- **Healthchecks** für alle wichtigen Dienste (Datenbank, Ledger, Backend, Oberfläche, Documenso, Sandbox, Mailpit, Ollama).
  `restart: unless-stopped` startet ausgefallene Dienste neu. Die Oberfläche startet erst, wenn das Backend gesund ist.
- **Postgres-Tuning** für kleine Server (2–4 GB RAM): `shared_buffers` 256 MB, `work_mem` 8 MB, SSD-optimierte Kosten.
  Die Datensicherheit bleibt unverändert, `fsync` und `synchronous_commit` sind an. Bei mehr RAM in `docker-compose.yml`
  anpassen, etwa `shared_buffers` auf ¼ des RAM.
- **Oberfläche:** Lesende Anfragen werden bei Netzfehlern oder 502/503/504, etwa beim Neustart, einmal nach 0,4 s
  wiederholt. Schreibende Anfragen werden nie automatisch wiederholt, damit nichts doppelt gebucht wird.

**Für noch mehr Verfügbarkeit** ist der Betrieb gefragt, nicht die Software:

- USV
- Server mit RAID/SSD
- tägliche Sicherung (Abschnitt Backup) mit regelmäßigem Wiederherstellungstest
- Archiv-Spiegel auf S3 mit Object Lock (Abschnitt 14)
- Überwachung der Adresse `/health` von außen (z. B. Uptime Kuma)
