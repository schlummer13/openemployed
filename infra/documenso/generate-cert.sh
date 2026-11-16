#!/bin/sh
# Erzeugt ein selbstsigniertes Zertifikat, mit dem Documenso fertige PDFs versiegelt.
# Für Produktion: Zertifikat einer anerkannten CA verwenden (Vertrauensanzeige in PDF-Readern).
# Aufruf: DOCUMENSO_SIGNING_PASSPHRASE=... ./infra/documenso/generate-cert.sh
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
: "${DOCUMENSO_SIGNING_PASSPHRASE:?DOCUMENSO_SIGNING_PASSPHRASE fehlt}"
SUBJECT="${DOCUMENSO_CERT_SUBJECT:-/C=DE/L=Duesseldorf/O=selbstlite/CN=selbstlite Signatur}"

if [ -f "$DIR/cert.p12" ]; then
  echo "cert.p12 existiert bereits – nichts zu tun."
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -subj "$SUBJECT" 2>/dev/null

openssl pkcs12 -export -out "$DIR/cert.p12" \
  -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -passout env:DOCUMENSO_SIGNING_PASSPHRASE

chmod 644 "$DIR/cert.p12"
echo "Signaturzertifikat erzeugt: $DIR/cert.p12"
