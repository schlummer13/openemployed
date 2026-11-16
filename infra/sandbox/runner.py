"""Führt einen Python-Schritt aus (eigener Prozess, Limits setzt server.py). Eingabe/Ausgabe als JSON über stdin/stdout."""

import builtins
import contextlib
import io
import ipaddress
import json
import socket
import sys

ALLOWED = {"requests", "urllib", "json", "math", "re", "datetime", "decimal", "statistics", "collections", "itertools", "functools", "string", "textwrap",
           "calendar", "random", "uuid", "hashlib", "base64", "zoneinfo", "unicodedata", "operator", "fractions"}
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED:
        raise ImportError(f"Modul {name} ist im Workflow nicht erlaubt (erlaubt: {', '.join(sorted(ALLOWED))})")
    return _real_import(name, globals, locals, fromlist, level)


def _blocked(host: str) -> bool:
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(host, None)]
        except OSError:
            return True
    return any(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
               for ip in ips)


def _audit(event: str, args: tuple) -> None:
    """Nicht abschaltbarer Schutz (PEP 578): kein Zugriff auf interne Netze, keine Prozesse, keine Dateien schreiben."""
    if event == "socket.connect":
        sock, address = args
        if sock.family not in (socket.AF_INET, socket.AF_INET6) or _blocked(str(address[0])):
            raise PermissionError(f"Verbindung zu {address!r} ist im Workflow nicht erlaubt (internes Netz)")
    elif event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "pty.spawn", "ctypes.dlopen"):
        raise PermissionError("Prozesse/Bibliotheken starten ist im Workflow nicht erlaubt")
    elif event == "open" and len(args) > 1 and isinstance(args[1], str) and any(m in args[1] for m in "wax+"):
        raise PermissionError("Dateien schreiben ist im Workflow nicht erlaubt")


def main() -> None:
    req = json.loads(sys.stdin.read())
    import requests  # noqa: F401 – vorab laden (Zertifikate, Module), danach greift der Schutz

    sys.addaudithook(_audit)
    safe_builtins = {k: getattr(builtins, k) for k in dir(builtins) if k not in ("open", "exec", "eval", "compile", "input", "breakpoint",
                                                                              "exit", "quit", "help", "__import__")}
    safe_builtins["__import__"] = _guarded_import
    env = {"__builtins__": safe_builtins, "data": req.get("input", {}), "result": None}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(req["code"], "<workflow>", "exec"), env)  # noqa: S102 – isolierter Prozess im Sandbox-Container
        result = env.get("result")
        json.dumps(result)  # muss als JSON darstellbar sein
        resp = {"ok": True, "result": result, "stdout": out.getvalue()[-10000:]}
    except BaseException as exc:  # noqa: BLE001
        resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000], "stdout": out.getvalue()[-10000:]}
    sys.__stdout__.write(json.dumps(resp, default=str))


if __name__ == "__main__":
    main()
