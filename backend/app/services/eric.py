"""Anbindung an ERiC (ELSTER Rich Client) der Finanzverwaltung über ctypes.

ERiC ist proprietär und wird nach Registrierung im ELSTER-Entwicklerbereich (kostenlos, mit Hersteller-ID) bezogen.
Einbinden: ERiC-Verzeichnis nach ``secrets/eric`` legen (im Container ``/opt/eric``), ``ERIC_HOME`` setzen.
Die Bibliothek arbeitet nicht parallel – alle Aufrufe laufen nacheinander in einem Hilfsthread.
Die PIN des ELSTER-Zertifikats existiert nur für die Dauer des Aufrufs und wird nicht gespeichert.

Hinweis: Diese Anbindung ist ohne ERiC-Bibliothek nicht testbar; vor dem Produktivbetrieb im Testmodus
(Testmerker 700000004) prüfen.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

ERIC_OK = 0
ERIC_VALIDIERE = 1 << 1
ERIC_SENDE = 1 << 2


class EricError(RuntimeError):
    pass


class EricNotInstalled(EricError):
    pass


class _CryptoParams(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("zertifikatHandle", ctypes.c_uint32), ("pin", ctypes.c_char_p)]


@dataclass(frozen=True)
class EricResult:
    ok: bool
    code: int
    response: str          # Rückgabe-XML (Prüfergebnis/Hinweise)
    server_response: str   # Serverantwort-XML (bei Versand)
    transfer_ticket: str | None
    error: str | None


def library_path(eric_home: str) -> Path | None:
    if not eric_home:
        return None
    for name in ("lib/libericapi.so", "lib/libericapi.dylib", "libericapi.so"):
        p = Path(eric_home) / name
        if p.exists():
            return p
    return None


class EricClient:
    _lock = threading.Lock()

    def __init__(self, eric_home: str, log_dir: str = "/tmp/eric-log") -> None:  # noqa: S108 – Protokollordner des ERiC im Container
        path = library_path(eric_home)
        if path is None:
            raise EricNotInstalled("ERiC ist nicht installiert (ERIC_HOME mit lib/libericapi.so)")
        self.home = Path(eric_home)
        self.path = path
        self.log_dir = log_dir

    def _call(self, xml: str, data_type: str, flags: int, cert_path: str | None, pin: str | None) -> EricResult:
        with self._lock:
            os.makedirs(self.log_dir, exist_ok=True)
            lib = ctypes.CDLL(str(self.path))
            lib.EricRueckgabepufferErzeugen.restype = ctypes.c_void_p
            lib.EricRueckgabepufferInhalt.restype = ctypes.c_char_p
            lib.EricRueckgabepufferInhalt.argtypes = [ctypes.c_void_p]
            lib.EricRueckgabepufferFreigeben.argtypes = [ctypes.c_void_p]
            rc = lib.EricInitialisiere(str(self.path.parent).encode(), self.log_dir.encode())
            if rc != ERIC_OK:
                raise EricError(f"ERiC-Initialisierung fehlgeschlagen (Code {rc})")
            buf_resp = lib.EricRueckgabepufferErzeugen()
            buf_server = lib.EricRueckgabepufferErzeugen()
            cert = ctypes.c_uint32(0)
            try:
                crypto = None
                if flags & ERIC_SENDE:
                    if not cert_path or not pin:
                        raise EricError("Für den Versand werden ELSTER-Zertifikat und PIN benötigt")
                    support = ctypes.c_uint32(0)
                    rc = lib.EricGetHandleToCertificate(ctypes.byref(cert), ctypes.byref(support), cert_path.encode())
                    if rc != ERIC_OK:
                        raise EricError(f"ELSTER-Zertifikat konnte nicht geöffnet werden (Code {rc})")
                    crypto = _CryptoParams(2, cert.value, pin.encode())
                transfer = ctypes.c_uint32(0)
                rc = lib.EricBearbeiteVorgang(xml.encode("utf-8"), data_type.encode(), ctypes.c_uint32(flags), None,
                                              ctypes.byref(crypto) if crypto is not None else None, ctypes.byref(transfer),
                                              ctypes.c_void_p(buf_resp), ctypes.c_void_p(buf_server))
                response = (lib.EricRueckgabepufferInhalt(buf_resp) or b"").decode("utf-8", "replace")
                server = (lib.EricRueckgabepufferInhalt(buf_server) or b"").decode("utf-8", "replace")
                error = None
                if rc != ERIC_OK:
                    err_buf = lib.EricRueckgabepufferErzeugen()
                    lib.EricHoleFehlerText(ctypes.c_int(rc), ctypes.c_void_p(err_buf))
                    error = (lib.EricRueckgabepufferInhalt(err_buf) or b"").decode("utf-8", "replace") or f"ERiC-Fehler {rc}"
                    lib.EricRueckgabepufferFreigeben(err_buf)
                ticket = re.search(r"<TransferTicket>([^<]+)</TransferTicket>", server)
                return EricResult(rc == ERIC_OK, rc, response, server, ticket.group(1) if ticket else None, error)
            finally:
                if cert.value:
                    lib.EricCloseHandleToCertificate(cert)
                lib.EricRueckgabepufferFreigeben(ctypes.c_void_p(buf_resp))
                lib.EricRueckgabepufferFreigeben(ctypes.c_void_p(buf_server))
                lib.EricBeende()

    async def validate(self, xml: str, data_type: str) -> EricResult:
        return await asyncio.to_thread(self._call, xml, data_type, ERIC_VALIDIERE, None, None)

    async def send(self, xml: str, data_type: str, cert_path: str, pin: str) -> EricResult:
        return await asyncio.to_thread(self._call, xml, data_type, ERIC_VALIDIERE | ERIC_SENDE, cert_path, pin)
