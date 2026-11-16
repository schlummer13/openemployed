# API und Webhooks
> REST-API mit API-Schlüsseln, Webhooks mit Signatur und die Entwickler-Dokumentation.

## REST-API
Alle Funktionen der Oberfläche sind über die REST-API unter `/api/…` erreichbar. Für Skripte und Tools wie n8n, Zapier oder Make
legt die Geschäftsführung unter *System & Integrationen → API-Schlüssel* einen Schlüssel an:
- **nur lesen** (Rolle Steuerberater) oder **lesen und schreiben** (Rolle Buchhaltung) – Verwaltung ist nie möglich
- optional mit Ablaufdatum, jederzeit widerrufbar; gespeichert wird nur ein Hash

```bash
curl -H "Authorization: Bearer sl_xxxx_…" https://app.firma.de/api/customers
```

Die vollständige Referenz aller Endpunkte mit Parametern und Datenstrukturen findet die Geschäftsführung unter **Entwickler-API**
(nur nach Anmeldung).

## Webhooks
Webhooks melden Ereignisse per HTTPS-POST an eure Systeme. Jede Zustellung trägt die Kopfzeilen `X-OpenEmployed-Event`,
`X-OpenEmployed-Delivery` und `X-OpenEmployed-Signature: t=<Unixzeit>,v1=<HMAC>`. Die Signatur ist HMAC-SHA256 mit dem
Webhook-Geheimnis über `<t>.<body>`. Fehlgeschlagene Zustellungen werden bis zu achtmal mit wachsendem Abstand wiederholt.

```python
import hmac, hashlib, time

def gueltig(secret: str, header: str, body: bytes) -> bool:
    teile = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(teile["t"])) > 300:
        return False
    erwartet = hmac.new(secret.encode(), f"{teile['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(erwartet, teile["v1"])
```

Die Zustellung erfolgt mindestens einmal – nutzt `delivery_id`, um doppelte Meldungen zu erkennen.
