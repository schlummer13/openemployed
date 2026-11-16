"""Archiv-Spiegelung: Ordner/Festplatte, Prüfung mit Manipulationserkennung, Vollexport (ZIP + Offline-Prüfung), optional S3 mit WORM."""

from __future__ import annotations

import datetime as dt
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.services import archive_sync
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


async def _upload(api, title: str, content: bytes) -> None:  # type: ignore[no-untyped-def]
    r = await api.post("/api/archive", data={"category": "correspondence", "title": title, "document_date": dt.datetime.now(dt.UTC).date().isoformat()},
                       files={"file": (f"{title}.pdf", content, "application/pdf")})
    assert r.status_code == 201, r.text


async def test_folder_mirror_verify_export(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    monkeypatch.setattr(archive_sync, "EXPORT_ROOT", tmp_path)
    for i in range(3):
        await _upload(api, f"Brief {i}", f"%PDF-1.4 Brief {i}".encode())

    r = await api.post("/api/archive/targets", json={"name": "USB-Platte", "kind": "folder", "config": {"subfolder": "archiv"}})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert (await api.post(f"/api/archive/targets/{tid}/test")).json()["ok"]
    res = (await api.post(f"/api/archive/targets/{tid}/sync")).json()
    assert res["copied"] == 3 and res["status"] == "ok"
    files = sorted(p for p in (tmp_path / "archiv").rglob("*.pdf"))
    assert len(files) == 3 and all((p.stat().st_mode & 0o222) == 0 for p in files), "schreibgeschützt abgelegt"
    assert list((tmp_path / "archiv" / "manifest").glob("manifest-*.csv"))
    # zweiter Lauf: nichts zu tun
    assert (await api.post(f"/api/archive/targets/{tid}/sync")).json()["copied"] == 0
    assert (await api.post(f"/api/archive/targets/{tid}/verify")).json() == {"ok": True, "checked": 3, "valid": 3, "problems": []}

    # Manipulation einer Kopie wird erkannt und beim nächsten Lauf neu gespiegelt
    files[0].chmod(0o644)
    files[0].write_bytes(b"%PDF-1.4 manipuliert")
    v = (await api.post(f"/api/archive/targets/{tid}/verify")).json()
    assert not v["ok"] and len(v["problems"]) == 1
    assert (await api.post(f"/api/archive/targets/{tid}/sync")).json()["copied"] == 1
    assert (await api.post(f"/api/archive/targets/{tid}/verify")).json()["ok"]
    t = next(x for x in (await api.get("/api/archive/targets")).json() if x["id"] == tid)
    assert t["copies"] == 3 and t["problems"] == 0 and t["documents"] == 3

    # Vollexport für die Festplatte: Dateien, Manifest, Offline-Prüfung
    z = await api.get("/api/archive/export")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    out = tmp_path / "export"
    zipfile.ZipFile(io.BytesIO(z.content)).extractall(out)
    assert {"manifest.csv", "pruefen.py", "index.html", "LIESMICH.txt"} <= {p.name for p in out.iterdir()}
    ok = subprocess.run([sys.executable, "-I", str(out / "pruefen.py")], capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and "0 Fehler" in ok.stdout
    next(out.rglob("*.pdf")).write_bytes(b"veraendert")
    bad = subprocess.run([sys.executable, "-I", str(out / "pruefen.py")], capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "VERÄNDERT" in bad.stdout

    # S3 nur mit https (außer lokaler Testserver)
    r = await api.post("/api/archive/targets", json={"name": "x", "kind": "s3", "config": {"endpoint": "http://evil.example", "bucket": "b"}})
    assert r.status_code == 409


@pytest.mark.skipif(not os.environ.get("S3_TEST_ENDPOINT"), reason="S3_TEST_ENDPOINT nicht gesetzt")
async def test_s3_worm_target(env: Env) -> None:  # noqa: F811
    """Gegen einen echten S3-Server mit Object Lock (z. B. SeaweedFS): Upload gesperrt, Prüfung ok."""
    api = env.api
    await setup_basics(env)
    await _upload(api, "WORM-Beleg", b"%PDF-1.4 worm")
    endpoint = os.environ["S3_TEST_ENDPOINT"]
    monkey_host = archive_sync.urlparse(endpoint).hostname
    assert monkey_host
    r = await api.post("/api/archive/targets", json={
        "name": "WORM", "kind": "s3", "object_lock": True, "config": {"endpoint": endpoint, "bucket": os.environ.get("S3_TEST_BUCKET", "oe-archiv"),
                                                                       "region": "us-east-1", "prefix": "test"},
        "secret": {"access_key": os.environ["S3_TEST_KEY"], "secret_key": os.environ["S3_TEST_SECRET"]}})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert (await api.post(f"/api/archive/targets/{tid}/test")).json()["ok"]
    assert (await api.post(f"/api/archive/targets/{tid}/sync")).json()["copied"] == 1
    assert (await api.post(f"/api/archive/targets/{tid}/verify")).json()["ok"]
    assert (await api.put(f"/api/archive/targets/{tid}", json={"name": "WORM", "kind": "s3", "object_lock": False,
                                                              "config": {"endpoint": endpoint, "bucket": "oe-archiv"}})).status_code == 409
