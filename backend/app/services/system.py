"""Systemstatus für die Verwaltung: Version, Datensicherungen, Integrität – ohne Shell-Zugriff aus der App."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from app import __version__

BACKUP_DIR = Path("/backups")


def backups(base: Path = BACKUP_DIR) -> dict[str, Any]:
    if not base.exists():
        return {"available": False, "files": [], "last_success": None, "last_failure": None, "requested": False}

    def read(name: str) -> str | None:
        f = base / name
        return f.read_text().strip() if f.exists() else None

    files = []
    for kind in ("daily", "monthly", "yearly"):
        for f in sorted((base / kind).glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            st = f.stat()
            sha = f.with_name(f.name + ".sha256")
            files.append({"kind": kind, "name": f.name, "size": st.st_size, "created": dt.datetime.fromtimestamp(st.st_mtime, dt.UTC),
                          "verified": sha.exists()})
    return {"available": True, "files": sorted(files, key=lambda x: x["created"], reverse=True)[:40], "last_success": read("last_success"),
            "last_failure": read("last_failure"), "requested": (base / ".request").exists()}


def request_backup(base: Path = BACKUP_DIR) -> None:
    if not base.exists():
        raise FileNotFoundError("Sicherungsverzeichnis nicht eingebunden")
    (base / ".request").write_text(dt.datetime.now(dt.UTC).isoformat())


def backup_file(name: str, base: Path = BACKUP_DIR) -> Path:
    if "/" in name or not name.endswith(".dump") or name.startswith("."):
        raise FileNotFoundError(name)
    for kind in ("daily", "monthly", "yearly"):
        p = base / kind / name
        if p.is_file():
            return p
    raise FileNotFoundError(name)


def info() -> dict[str, Any]:
    from app.core.config import get_settings

    s = get_settings()
    return {"version": __version__, "env": s.app_env, "update_command": "make update", "restore_command": "scripts/restore.sh <datei.dump>"}
