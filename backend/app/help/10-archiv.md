# Dokumentenarchiv
> Revisionssichere Ablage mit Hash-Kette, Aufbewahrungsfristen und Volltextsuche.

## Was archiviert wird
Automatisch (stündlich): Ausgangsrechnungen, gebuchte Belege, Mahnungen, signierte Verträge, versendete Angebote,
Entgeltabrechnungen, SEPA-Dateien und jede versendete E-Mail (.eml). Eigene Dokumente (Verträge, Briefe, Bescheide) ladet ihr
über **+ Dokument** hoch.

## Unveränderlich
Jedes Dokument bekommt eine Prüfsumme (SHA-256) und einen Kettenhash über alle Vorgänger. Inhalt, Datum, Kategorie und Frist sind
von der Datenbank gesperrt; ändern lassen sich nur Titel, Schlagworte und Notiz (protokolliert). **Integrität prüfen** rechnet die
gesamte Kette nach.

## Fristen
Seit 2025 gelten 8 Jahre für Buchungsbelege (Rechnungen, Belege, Lohn, Bank), 10 Jahre für Bücher, Abschlüsse und Steuerunterlagen,
6 Jahre für Geschäftsbriefe. Die Frist beginnt mit dem Ende des Kalenderjahres. Nach Ablauf kann der Inhalt vernichtet werden –
Metadaten und Prüfsumme bleiben als Nachweis.

## Suche
Die Volltextsuche durchsucht Titel, Partner, Nummern und den Text der Dokumente (auch PDFs) und markiert die Fundstelle.

## Spiegelung auf externe Speicher
Unter **Archiv → Spiegelung & Export** richtet ihr Ziele ein, auf die jede Nacht um 02:00 alle neuen Dokumente kopiert werden:

- **S3-Speicher** (AWS, Wasabi, Backblaze B2, IONOS, Hetzner Object Storage, SeaweedFS …) – mit **Object Lock (WORM)** ist jede
  Kopie bis zum Ende ihrer Aufbewahrungsfrist technisch unlöschbar und unveränderbar, auch für Administratoren oder bei gestohlenen
  Zugangsdaten. Der Bucket muss beim Anlegen mit „Object Lock“ erstellt werden; Modus COMPLIANCE empfohlen; Region in der EU.
- **WebDAV** – Nextcloud, ownCloud, Synology/QNAP, Hetzner Storage Box.
- **Ordner / Festplatte** – eine eingehängte USB-Platte oder NAS-Freigabe auf dem Server (in der `.env` `ARCHIVE_EXPORT_PATH`
  setzen). Dateien werden schreibgeschützt abgelegt.

Je Dokument entstehen die Originaldatei und eine `.json` mit Prüfsumme, Kettenhash, Frist und Quelle; jeder Lauf schreibt ein
Manifest. **Verbindung testen** prüft Zugang und Object Lock, **Alle Kopien prüfen** vergleicht jede Kopie mit der Prüfsumme.
Zusätzlich prüft der nächtliche Lauf die komplette Hash-Kette und eine Stichprobe der Kopien; Fehler erscheinen rot unter „Zu
erledigen“, fehlende oder veränderte Kopien werden neu gespiegelt. Nach Fristablauf vernichtete Dokumente werden auf Zielen ohne
Object Lock ebenfalls entfernt.

## Auf Festplatte herunterladen
**ZIP herunterladen** erzeugt einen vollständigen, prüfbaren Export (alle Jahre oder ein Jahr): Originaldateien je Jahr und
Kategorie, `manifest.csv` mit allen Prüfsummen und der Hash-Kette, `pruefen.py` (prüft offline mit `python3 pruefen.py`, dass keine
Datei verändert wurde und die Kette lückenlos ist) und `index.html` als durchsuchbare Übersicht im Browser. Für eine
Betriebsprüfung (§ 147 Abs. 6 AO) zusätzlich den GoBD-Export der Buchhaltung beilegen.

