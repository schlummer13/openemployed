#!/bin/sh
# Startet den Ollama-Server und lädt das konfigurierte Modell beim ersten Start automatisch herunter.
# Das Modell liegt im Volume ollama-data und wird nur einmal geladen (Qwen 2.5 VL 7B ≈ 6 GB, 3B ≈ 3 GB).
set -eu
MODEL="${OLLAMA_MODEL:-qwen2.5vl:7b}"

ollama serve &
SERVER=$!

until ollama list >/dev/null 2>&1; do sleep 1; done

if ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
  echo "Modell $MODEL ist vorhanden."
else
  echo "Lade Modell $MODEL herunter (einmalig) …"
  # bei Netzwerkfehlern erneut versuchen, der Server läuft währenddessen weiter
  until ollama pull "$MODEL"; do echo "Download fehlgeschlagen – neuer Versuch in 30 s"; sleep 30; done
  echo "Modell $MODEL ist bereit."
fi

wait "$SERVER"
