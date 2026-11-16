"""Sandbox für Python-Schritte der Workflows.

Isolation: eigener Container ohne Netzwerkzugang nach außen (internes Docker-Netz), schreibgeschütztes Dateisystem,
unprivilegierter Benutzer; jeder Schritt läuft in einem neuen Prozess mit CPU-, Speicher-, Datei- und Zeitlimit.
Zugriff nur mit gemeinsamem Token aus dem Backend.
"""

import hmac
import json
import os
import resource
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("SANDBOX_TOKEN", "")
MAX_BODY = 256 * 1024
WALL_SECONDS = 20


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run" or not TOKEN or not hmac.compare_digest(self.headers.get("X-Sandbox-Token", ""), TOKEN):
            return self._send(403, {"ok": False, "error": "verboten"})
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY:
            return self._send(413, {"ok": False, "error": "Eingabe zu groß"})
        body = self.rfile.read(length)
        try:
            proc = subprocess.run([sys.executable, "-I", "/app/runner.py"], input=body, capture_output=True, timeout=WALL_SECONDS,
                                  preexec_fn=_limits, env={}, cwd="/tmp", check=False)  # noqa: S603, PLW1509
        except subprocess.TimeoutExpired:
            return self._send(200, {"ok": False, "error": f"Zeitlimit von {WALL_SECONDS} Sekunden überschritten"})
        try:
            if not proc.stdout.strip():
                raise json.JSONDecodeError("leer", "", 0)
            self._send(200, json.loads(proc.stdout[-300_000:]))
        except json.JSONDecodeError:
            msg = "CPU-Limit (5 s) oder Speicherlimit überschritten" if proc.returncode < 0 else (proc.stderr.decode(errors="replace")[-1000:] or "Fehler")
            self._send(200, {"ok": False, "error": msg})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("sandbox: " + fmt % args + "\n")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104 – nur im internen Docker-Netz
