"""Archiv-Spiegelung (GoBD Rz. 103 ff., 119 ff.): Kopien aller Archivdokumente auf externe Ziele, Prüfung, Vollexport.

Ziele:
- ``s3``: beliebiger S3-kompatibler Speicher. Mit **Object Lock (COMPLIANCE)** ist jedes Objekt bis zum Ende der Aufbewahrungsfrist
  technisch unlöschbar und unveränderbar (WORM) – auch für Administratoren und bei kompromittierten Zugangsdaten.
- ``webdav``: Nextcloud, ownCloud, NAS (Synology/QNAP), Hetzner Storage Box.
- ``folder``: Verzeichnis im Container (``/exports``), z. B. eine eingehängte USB-Festplatte oder ein NAS-Mount.

Je Dokument: ``<präfix>/<jahr>/<kategorie>/<nummer>_<datei>`` plus ``.json`` mit Metadaten (SHA-256, Kettenhash, Frist, Quelle).
Jeder Lauf schreibt zusätzlich ein Manifest (CSV) mit allen Dokumenten. Die Prüfung vergleicht Größe/Prüfsumme jeder Kopie.
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

log = logging.getLogger("openemployed.archive_sync")
EXPORT_ROOT = Path(os.environ.get("ARCHIVE_EXPORT_ROOT", "/exports"))
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class TargetError(RuntimeError):
    pass


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:120] or "datei"


def location(doc: dict[str, Any], prefix: str) -> str:
    name = f"{doc['number']}_{_safe(doc['filename'])}"
    parts = [p for p in (prefix.strip("/"), str(doc["document_date"].year), doc["category"], name) if p]
    return "/".join(parts)


def sidecar(doc: dict[str, Any]) -> bytes:
    return json.dumps({k: (str(v) if v is not None else None) for k, v in doc.items() if k not in ("content", "text_content")},
                      ensure_ascii=False, indent=2).encode()


# ───────────────────────── Geheimnisse ─────────────────────────

def encrypt(secret: dict[str, str]) -> str:
    from app.services.settings_store import _fernet

    token: bytes = _fernet().encrypt(json.dumps(secret).encode())
    return token.decode()


def decrypt(blob: str | None) -> dict[str, str]:
    if not blob:
        return {}
    from app.services.settings_store import _fernet

    data: dict[str, str] = json.loads(_fernet().decrypt(blob.encode()))
    return data


# ───────────────────────── S3 (SigV4) ─────────────────────────

class S3:
    def __init__(self, endpoint: str, bucket: str, region: str, access_key: str, secret_key: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket, self.region, self.ak, self.sk = bucket, region or "us-east-1", access_key, secret_key
        self.host = urlparse(self.endpoint).netloc

    def _sign(self, method: str, key: str, headers: dict[str, str], payload_hash: str, query: str = "") -> dict[str, str]:
        now = dt.datetime.now(dt.UTC)
        amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = "/" + quote(self.bucket) + ("/" + quote(key, safe="/~") if key else "")
        h = {**{k.lower(): v.strip() for k, v in headers.items()}, "host": self.host, "x-amz-date": amz,
             "x-amz-content-sha256": payload_hash}
        signed = ";".join(sorted(h))
        canonical = "\n".join([method, path, query, "".join(f"{k}:{h[k]}\n" for k in sorted(h)), signed, payload_hash])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = hmac.new(f"AWS4{self.sk}".encode(), day.encode(), hashlib.sha256).digest()
        for part in (self.region, "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        h["authorization"] = f"AWS4-HMAC-SHA256 Credential={self.ak}/{scope}, SignedHeaders={signed}, Signature={sig}"
        return h

    def _url(self, key: str, query: str = "") -> str:
        return f"{self.endpoint}/{quote(self.bucket)}" + (f"/{quote(key, safe='/~')}" if key else "") + (f"?{query}" if query else "")

    async def put(self, key: str, body: bytes, content_type: str, *, lock_mode: str | None = None,
                  retain_until: dt.date | None = None) -> str:
        md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324 – von S3 für Object Lock verlangt
        headers = {"content-type": content_type, "content-md5": md5}
        if lock_mode and retain_until:
            headers["x-amz-object-lock-mode"] = lock_mode
            headers["x-amz-object-lock-retain-until-date"] = f"{retain_until + dt.timedelta(days=1):%Y-%m-%d}T00:00:00Z"
        signed = self._sign("PUT", key, headers, hashlib.sha256(body).hexdigest())
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(self._url(key), content=body, headers=signed)
        if r.status_code >= 300:
            raise TargetError(f"S3 PUT {r.status_code}: {r.text[:300]}")
        etag: str = r.headers.get("etag", "")
        return etag.strip('"')

    async def head(self, key: str) -> dict[str, str] | None:
        signed = self._sign("HEAD", key, {}, hashlib.sha256(b"").hexdigest())
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.head(self._url(key), headers=signed)
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise TargetError(f"S3 HEAD {r.status_code}")
        return {k.lower(): v for k, v in r.headers.items()}

    async def delete(self, key: str) -> None:
        signed = self._sign("DELETE", key, {}, hashlib.sha256(b"").hexdigest())
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.delete(self._url(key), headers=signed)
        if r.status_code >= 300 and r.status_code != 404:
            raise TargetError(f"S3 DELETE {r.status_code}: {r.text[:200]}")

    async def object_lock_enabled(self) -> bool:
        signed = self._sign("GET", "", {}, hashlib.sha256(b"").hexdigest(), query="object-lock=")
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(self._url("", "object-lock="), headers=signed)
        if r.status_code == 404 or "ObjectLockConfigurationNotFound" in r.text:
            return False
        if r.status_code >= 300:
            raise TargetError(f"S3-Bucket nicht erreichbar ({r.status_code}): {r.text[:200]}")
        return re.search(r"<ObjectLockEnabled[^>]*>\s*Enabled\s*</ObjectLockEnabled>", r.text) is not None


# ───────────────────────── WebDAV ─────────────────────────

class WebDAV:
    def __init__(self, url: str, username: str, password: str) -> None:
        self.base = url.rstrip("/")
        self.auth = (username, password)

    async def _mkcols(self, c: httpx.AsyncClient, key: str) -> None:
        parts = key.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            r = await c.request("MKCOL", f"{self.base}/{quote('/'.join(parts[:i]))}/", auth=self.auth)
            if r.status_code not in (201, 405, 301, 409):
                raise TargetError(f"WebDAV MKCOL {r.status_code}")

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            await self._mkcols(c, key)
            r = await c.put(f"{self.base}/{quote(key)}", content=body, headers={"content-type": content_type}, auth=self.auth)
        if r.status_code >= 300:
            raise TargetError(f"WebDAV PUT {r.status_code}: {r.text[:200]}")

    async def head(self, key: str) -> dict[str, str] | None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.head(f"{self.base}/{quote(key)}", auth=self.auth)
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise TargetError(f"WebDAV HEAD {r.status_code}")
        return {k.lower(): v for k, v in r.headers.items()}

    async def get(self, key: str) -> bytes:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{self.base}/{quote(key)}", auth=self.auth)
        if r.status_code >= 300:
            raise TargetError(f"WebDAV GET {r.status_code}")
        return r.content

    async def delete(self, key: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.delete(f"{self.base}/{quote(key)}", auth=self.auth)
        if r.status_code >= 300 and r.status_code != 404:
            raise TargetError(f"WebDAV DELETE {r.status_code}")


# ───────────────────────── Ordner ─────────────────────────

class Folder:
    def __init__(self, sub: str) -> None:
        root = EXPORT_ROOT.resolve()
        self.base = (root / _safe(sub or "archiv")).resolve()
        if root not in self.base.parents and self.base != root:
            raise TargetError("Ungültiger Ordner")

    def check(self) -> None:
        if not EXPORT_ROOT.exists():
            raise TargetError(f"{EXPORT_ROOT} ist nicht eingehängt (docker-compose: ARCHIVE_EXPORT_PATH)")
        self.base.mkdir(parents=True, exist_ok=True)
        probe = self.base / ".schreibtest"
        probe.write_bytes(b"ok")
        probe.unlink()

    def path(self, key: str) -> Path:
        p = (self.base / key).resolve()
        if self.base not in p.parents:
            raise TargetError("Ungültiger Pfad")
        return p

    def put(self, key: str, body: bytes) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            if hashlib.sha256(p.read_bytes()).hexdigest() == hashlib.sha256(body).hexdigest():
                return
            p.chmod(0o644)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(p)
        p.chmod(0o444)  # schreibgeschützt

    def sha256(self, key: str) -> str | None:
        p = self.path(key)
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

    def delete(self, key: str) -> None:
        for p in (self.path(key), self.path(key + ".json")):
            if p.exists():
                p.chmod(0o644)
                p.unlink()


# ───────────────────────── Ziele verwalten ─────────────────────────

TARGET_COLUMNS = ("id, name, kind, config, object_lock, lock_mode, schedule, enabled, last_run_at, last_status, last_message, "
                  "last_verified_at, created_at")


async def targets(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        f"SELECT {TARGET_COLUMNS}, (SELECT count(*) FROM archive_replicas r WHERE r.target_id = t.id AND r.status = 'ok') AS copies, "
        "(SELECT count(*) FROM archive_replicas r WHERE r.target_id = t.id AND r.status IN ('error', 'missing')) AS problems "
        "FROM archive_targets t WHERE tenant_id = :t ORDER BY created_at"), {"t": tenant_id})).mappings().all()
    total = await conn.scalar(text("SELECT count(*) FROM archive_documents WHERE tenant_id = :t AND purged_at IS NULL"), {"t": tenant_id})
    return [dict(r) | {"documents": total} for r in rows]


def _client(t: dict[str, Any], secret: dict[str, str]) -> S3 | WebDAV | Folder:
    cfg = t["config"]
    if t["kind"] == "s3":
        return S3(cfg["endpoint"], cfg["bucket"], cfg.get("region", ""), secret.get("access_key", ""), secret.get("secret_key", ""))
    if t["kind"] == "webdav":
        return WebDAV(cfg["url"], cfg.get("username", ""), secret.get("password", ""))
    return Folder(cfg.get("subfolder", "archiv"))


async def _target(conn: AsyncConnection, tenant_id: UUID, target_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {TARGET_COLUMNS}, secret_enc FROM archive_targets WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": target_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Ziel nicht gefunden")
    return dict(row)


async def save_target(conn: AsyncConnection, tenant_id: UUID, target_id: UUID | None, data: dict[str, Any], actor: str) -> UUID:
    cfg = {k: v for k, v in (data.get("config") or {}).items() if v not in (None, "")}
    secret = {k: v for k, v in (data.get("secret") or {}).items() if v}
    if data["kind"] == "s3" and not all(cfg.get(k) for k in ("endpoint", "bucket")):
        raise ConflictError("Endpoint und Bucket angeben")
    if data["kind"] == "webdav" and not cfg.get("url"):
        raise ConflictError("WebDAV-Adresse angeben")
    for key in ("endpoint", "url"):
        local_ok = {"localhost", "seaweedfs", "minio", *filter(None, os.environ.get("ARCHIVE_ALLOW_HTTP_HOSTS", "").split(","))}
        if cfg.get(key) and urlparse(cfg[key]).scheme != "https" and urlparse(cfg[key]).hostname not in local_ok:
            raise ConflictError("Nur verschlüsselte Verbindungen (https://)")
    params = {"t": tenant_id, "n": data["name"], "k": data["kind"], "c": json.dumps(cfg),
              "ol": bool(data.get("object_lock")) and data["kind"] == "s3",
              "lm": data.get("lock_mode") or "COMPLIANCE", "s": data.get("schedule") or "daily", "e": data.get("enabled", True), "a": actor}
    if target_id is None:
        tid: UUID = await conn.scalar(text(
            "INSERT INTO archive_targets (tenant_id, name, kind, config, secret_enc, object_lock, lock_mode, schedule, enabled, "
            "created_by) VALUES (:t, :n, :k, CAST(:c AS jsonb), :se, :ol, :lm, :s, :e, :a) RETURNING id"),
            params | {"se": encrypt(secret) if secret else None})
    else:
        old = await _target(conn, tenant_id, target_id)
        if old["object_lock"] and not params["ol"]:
            raise ConflictError("Object Lock kann nicht abgeschaltet werden – bestehende Kopien bleiben gesperrt")
        merged = decrypt(old["secret_enc"]) | secret
        await conn.execute(text(
            "UPDATE archive_targets SET name = :n, config = CAST(:c AS jsonb), secret_enc = :se, object_lock = :ol, lock_mode = :lm, "
            "schedule = :s, enabled = :e WHERE tenant_id = :t AND id = :id"),
            params | {"id": target_id, "se": encrypt(merged) if merged else None})
        tid = target_id
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="archive.target_saved", entity_type="archive_target", entity_id=tid,
                       after={"name": data["name"], "kind": data["kind"], "object_lock": params["ol"], **cfg})
    return tid


async def delete_target(conn: AsyncConnection, tenant_id: UUID, target_id: UUID, actor: str) -> None:
    t = await _target(conn, tenant_id, target_id)
    await conn.execute(text("DELETE FROM archive_targets WHERE id = :id"), {"id": target_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="archive.target_deleted", entity_type="archive_target",
                       entity_id=target_id, before={"name": t["name"], "kind": t["kind"]})


async def test_target(conn: AsyncConnection, tenant_id: UUID, target_id: UUID) -> dict[str, Any]:
    t = await _target(conn, tenant_id, target_id)
    client = _client(t, decrypt(t["secret_enc"]))
    try:
        if isinstance(client, S3):
            lock = await client.object_lock_enabled()
            if t["object_lock"] and not lock:
                return {"ok": False, "message": "Bucket ohne Object Lock – beim Anlegen des Buckets „Object Lock“ aktivieren "
                                                "(nachträglich nicht möglich)"}
            return {"ok": True, "message": "Verbindung ok" + (" · Object Lock aktiv (WORM)" if lock else " · ohne Object Lock")}
        if isinstance(client, WebDAV):
            key = f"{t['config'].get('prefix', 'openemployed').strip('/')}/.verbindungstest.txt"
            await client.put(key, b"OpenEmployed Verbindungstest", "text/plain")
            await client.delete(key)
            return {"ok": True, "message": "Verbindung ok · Schreiben möglich"}
        client.check()
        return {"ok": True, "message": f"Ordner beschreibbar: {client.base}"}
    except (TargetError, httpx.HTTPError, OSError) as exc:
        return {"ok": False, "message": str(exc)[:300]}


# ───────────────────────── Spiegeln und prüfen ─────────────────────────

async def sync_target(conn: AsyncConnection, tenant_id: UUID, target_id: UUID, *, limit: int = 500) -> dict[str, Any]:
    t = await _target(conn, tenant_id, target_id)
    client = _client(t, decrypt(t["secret_enc"]))
    prefix = t["config"].get("prefix", "openemployed") if t["kind"] != "folder" else ""
    if isinstance(client, Folder):
        client.check()
    docs = (await conn.execute(text(
        "SELECT d.id, d.seq, d.number, d.category, d.title, d.document_date, d.retention_years, d.retain_until, d.filename, d.mime_type, "
        "d.size, d.sha256, d.chain_hash, d.source_type, d.source_id, d.party, d.created_by, d.created_at, d.content "
        "FROM archive_documents d WHERE d.tenant_id = :t AND d.purged_at IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM archive_replicas r WHERE r.target_id = :tg AND r.document_id = d.id AND r.status = 'ok') ORDER BY d.seq LIMIT :l"),
        {"t": tenant_id, "tg": target_id, "l": limit})).mappings().all()
    copied, failed = 0, 0
    for d in docs:
        doc = dict(d)
        key = location(doc, prefix)
        body = bytes(doc["content"])
        md5 = hashlib.md5(body).hexdigest()  # noqa: S324 – Vergleich mit dem S3-ETag
        try:
            lock = (t["lock_mode"], doc["retain_until"]) if t["object_lock"] else (None, None)
            if isinstance(client, S3):
                await client.put(key, body, doc["mime_type"], lock_mode=lock[0], retain_until=lock[1])
                await client.put(key + ".json", sidecar(doc), "application/json", lock_mode=lock[0], retain_until=lock[1])
            elif isinstance(client, WebDAV):
                await client.put(key, body, doc["mime_type"])
                await client.put(key + ".json", sidecar(doc), "application/json")
            else:
                client.put(key, body)
                client.put(key + ".json", sidecar(doc))
            status, error = "ok", None
            copied += 1
        except (TargetError, httpx.HTTPError, OSError) as exc:
            status, error = "error", str(exc)[:300]
            failed += 1
        await conn.execute(text(
            "INSERT INTO archive_replicas (tenant_id, target_id, document_id, location, sha256, md5, size, locked_until, status, error) "
            "VALUES (:t, :tg, :d, :l, :h, :m, :s, :lu, :st, :e) ON CONFLICT (target_id, document_id) DO UPDATE SET "
            "location = EXCLUDED.location, "
            "sha256 = EXCLUDED.sha256, md5 = EXCLUDED.md5, status = EXCLUDED.status, error = EXCLUDED.error, replicated_at = now(), "
            "locked_until = EXCLUDED.locked_until"),
            {"t": tenant_id, "tg": target_id, "d": doc["id"], "l": key, "h": doc["sha256"], "m": md5, "s": doc["size"],
             "lu": doc["retain_until"] if t["object_lock"] else None, "st": status, "e": error})
        if failed >= 5 and not copied:
            break  # Ziel offensichtlich nicht erreichbar
    # Vernichtete Dokumente auf nicht gesperrten Zielen ebenfalls entfernen
    purged = 0
    if not t["object_lock"]:
        for r in (await conn.execute(text(
                "SELECT r.id, r.location FROM archive_replicas r JOIN archive_documents d ON d.id = r.document_id "
                "WHERE r.target_id = :tg AND r.status = 'ok' AND d.purged_at IS NOT NULL"), {"tg": target_id})).mappings():
            try:
                if isinstance(client, Folder):
                    client.delete(r["location"])
                else:
                    await client.delete(r["location"])
                    await client.delete(r["location"] + ".json")
                await conn.execute(text("UPDATE archive_replicas SET status = 'purged' WHERE id = :id"), {"id": r["id"]})
                purged += 1
            except (TargetError, httpx.HTTPError, OSError):
                log.warning("Vernichtung auf Ziel fehlgeschlagen: %s", r["location"])
    if copied or not docs:
        manifest = await manifest_csv(conn, tenant_id)
        name = f"{prefix + '/' if prefix else ''}manifest/manifest-{dt.datetime.now(dt.UTC):%Y%m%d-%H%M%S}.csv"
        try:
            if isinstance(client, (S3, WebDAV)):
                await client.put(name, manifest, "text/csv")
            else:
                client.put(name, manifest)
        except (TargetError, httpx.HTTPError, OSError) as exc:
            log.warning("Manifest nicht geschrieben: %s", exc)
    status = "error" if failed else "ok"
    message = f"{copied} kopiert" + (f", {failed} fehlgeschlagen" if failed else "") + (f", {purged} vernichtet" if purged else "")
    if not docs and not purged:
        message = "alles aktuell"
    await conn.execute(text("UPDATE archive_targets SET last_run_at = now(), last_status = :s, last_message = :m WHERE id = :id"),
                       {"s": status, "m": message, "id": target_id})
    return {"status": status, "copied": copied, "failed": failed, "purged": purged, "message": message, "remaining": len(docs) == limit}


async def verify_target(conn: AsyncConnection, tenant_id: UUID, target_id: UUID, *, sample: int | None = None) -> dict[str, Any]:
    """Prüft jede (oder eine Stichprobe der) Kopie: vorhanden, Größe, Prüfsumme (S3: ETag = MD5; Ordner: SHA-256), Sperrfrist."""
    t = await _target(conn, tenant_id, target_id)
    client = _client(t, decrypt(t["secret_enc"]))
    rows = (await conn.execute(text(
        "SELECT id, location, sha256, md5, size, locked_until FROM archive_replicas WHERE target_id = :tg "
        "AND status IN ('ok', 'missing', 'error') "
        + ("ORDER BY random() LIMIT :n" if sample else "ORDER BY replicated_at") ), {"tg": target_id, "n": sample})).mappings().all()
    ok, problems = 0, []
    for r in rows:
        try:
            if isinstance(client, Folder):
                good = client.sha256(r["location"]) == r["sha256"]
            elif isinstance(client, S3):
                h = await client.head(r["location"])
                good = h is not None and h.get("etag", "").strip('"') == r["md5"] and int(h.get("content-length", -1)) == r["size"]
                if good and h is not None and t["object_lock"] and not h.get("x-amz-object-lock-retain-until-date"):
                    good = False
            else:
                h = await client.head(r["location"])
                good = h is not None and int(h.get("content-length", -1)) == r["size"]
                if good and sample:  # Stichprobe zusätzlich inhaltlich prüfen
                    good = hashlib.sha256(await client.get(r["location"])).hexdigest() == r["sha256"]
        except (TargetError, httpx.HTTPError, OSError, ValueError):
            good = False
        await conn.execute(text("UPDATE archive_replicas SET status = :s, verified_at = now(), error = :e WHERE id = :id"),
                           {"s": "ok" if good else "missing", "e": None if good else "Kopie fehlt oder weicht ab", "id": r["id"]})
        if good:
            ok += 1
        else:
            problems.append(r["location"])
    await conn.execute(text("UPDATE archive_targets SET last_verified_at = now() WHERE id = :id"), {"id": target_id})
    return {"ok": not problems, "checked": len(rows), "valid": ok, "problems": problems[:50]}


async def run_scheduled(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> dict[str, Any]:
    """Täglicher Lauf: Archivkette prüfen, fällige Ziele spiegeln, Stichprobe der Kopien prüfen."""
    from app.services import archive

    chain = await archive.verify(conn, tenant_id)
    results: dict[str, Any] = {"chain": chain, "targets": {}}
    for t in await targets(conn, tenant_id):
        if not t["enabled"] or t["schedule"] == "manual" or (t["schedule"] == "weekly" and today.weekday() != 0):
            continue
        try:
            res = await sync_target(conn, tenant_id, t["id"])
            res["verify"] = await verify_target(conn, tenant_id, t["id"], sample=25)
        except (TargetError, httpx.HTTPError, OSError) as exc:
            res = {"status": "error", "message": str(exc)[:200]}
            await conn.execute(text("UPDATE archive_targets SET last_run_at = now(), last_status = 'error', last_message = :m "
                                    "WHERE id = :id"),
                               {"m": res["message"], "id": t["id"]})
        results["targets"][t["name"]] = res
    return results


# ───────────────────────── Manifest und Vollexport ─────────────────────────

async def manifest_csv(conn: AsyncConnection, tenant_id: UUID) -> bytes:
    rows = await conn.execute(text(
        "SELECT seq, number, category, title, document_date, retain_until, filename, mime_type, size, sha256, chain_hash, source_type, "
        "source_id, party, created_at, purged_at FROM archive_documents WHERE tenant_id = :t ORDER BY seq"), {"t": tenant_id})
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Lfd", "Nummer", "Kategorie", "Titel", "Belegdatum", "Aufbewahren bis", "Datei", "Typ", "Bytes", "SHA-256", "Kettenhash",
                "Quelle", "Quell-ID", "Partner", "Archiviert am", "Vernichtet am"])
    for r in rows:
        w.writerow([("" if v is None else v) for v in r])
    return ("﻿" + out.getvalue()).encode()


VERIFY_PY = '''#!/usr/bin/env python3
"""Prüft diesen Archivexport offline: SHA-256 jeder Datei und die Hash-Kette (GENESIS → Kettenhash). Aufruf: python3 pruefen.py"""
import csv, hashlib, pathlib, sys

root = pathlib.Path(__file__).parent
prev, errors, n = "0" * 64, 0, 0
with open(root / "manifest.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f, delimiter=";"))
for r in rows:
    n += 1
    chain = hashlib.sha256(f"{prev}:{r['Lfd']}:{r['SHA-256']}".encode()).hexdigest()
    if chain != r["Kettenhash"]:
        print("KETTE GEBROCHEN bei", r["Nummer"]); errors += 1
    prev = r["Kettenhash"]
    if r["Vernichtet am"] or not r.get("Pfad"):
        continue
    p = root / r["Pfad"]
    if not p.exists():
        print("FEHLT", r["Pfad"]); errors += 1
    elif hashlib.sha256(p.read_bytes()).hexdigest() != r["SHA-256"]:
        print("VERÄNDERT", r["Pfad"]); errors += 1
print(f"{n} Einträge geprüft, {errors} Fehler")
sys.exit(1 if errors else 0)
'''


async def export_zip(conn: AsyncConnection, tenant_id: UUID, *, year: int | None, actor: str) -> tuple[str, str]:
    """Vollständiger, prüfbarer Export (alle bzw. ein Jahr) als ZIP-Datei auf der Festplatte des Servers (temporär)."""
    from html import escape

    fd, path = tempfile.mkstemp(prefix="archiv-", suffix=".zip")
    os.close(fd)
    rows = await conn.stream(text(
        "SELECT seq, number, category, title, document_date, retain_until, filename, mime_type, size, sha256, chain_hash, source_type, "
        "source_id, party, created_at, purged_at, content FROM archive_documents WHERE tenant_id = :t ORDER BY seq"), {"t": tenant_id})
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Lfd", "Nummer", "Kategorie", "Titel", "Belegdatum", "Aufbewahren bis", "Datei", "Typ", "Bytes", "SHA-256", "Kettenhash",
                "Quelle", "Quell-ID", "Partner", "Archiviert am", "Vernichtet am", "Pfad"])
    table, count, total = [], 0, 0
    stamp = f"{dt.datetime.now(dt.UTC).astimezone():%d.%m.%Y %H:%M}"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        async for r in rows:
            in_scope = year is None or r.document_date.year == year
            rel = ""
            if in_scope and r.content is not None:
                rel = "dokumente/" + location({"document_date": r.document_date, "category": r.category, "number": r.number,
                                                "filename": r.filename}, "")
                z.writestr(rel, bytes(r.content))
                count += 1
                total += r.size
            # Manifest immer vollständig (für die Kettenprüfung), Dateien nur im gewählten Zeitraum
            w.writerow([r.seq, r.number, r.category, r.title, r.document_date, r.retain_until, r.filename, r.mime_type, r.size, r.sha256,
                        r.chain_hash, r.source_type or "", r.source_id or "", r.party or "", r.created_at, r.purged_at or "", rel])
            if in_scope:
                link = f'<a href="{escape(rel)}">öffnen</a>' if rel else "vernichtet"
                table.append(f"<tr><td>{r.number}</td><td>{r.document_date:%d.%m.%Y}</td><td>{escape(r.category)}</td>"
                             f"<td>{escape(r.title)}</td><td>{escape(r.party or '')}</td><td>{link}</td></tr>")
        z.writestr("manifest.csv", "﻿" + out.getvalue())
        z.writestr("pruefen.py", VERIFY_PY)
        z.writestr("index.html", "<!doctype html><html lang='de'><meta charset='utf-8'><title>Archivexport</title><style>"
                   "body{font:14px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;"
                   "padding:4px 6px;text-align:left}input{padding:6px;width:320px;margin-bottom:12px}</style>"
                   f"<h1>Dokumentenarchiv{f' {year}' if year else ''}</h1><p>{count} Dokumente · erstellt {stamp} "
                   "· Prüfung: <code>python3 pruefen.py</code></p><input placeholder='Suchen …' "
                   "oninput=\"for(const r of document.querySelectorAll"
                   "('tbody tr'))r.style.display=r.textContent.toLowerCase().includes(this.value.toLowerCase())?'':'none'\">"
                   "<table><thead><tr><th>Nummer</th><th>Datum</th><th>Kategorie</th><th>Titel</th><th>Partner</th><th></th></tr></thead><tbody>"
                   + "".join(table) + "</tbody></table></html>")
        z.writestr("LIESMICH.txt", (
            "Export des revisionssicheren Dokumentenarchivs (OpenEmployed)\r\n\r\n"
            "- dokumente/: Originaldateien je Jahr und Kategorie (Dateiname beginnt mit der Archivnummer)\r\n"
            "- manifest.csv: alle Archiveinträge mit SHA-256 und Kettenhash (lückenlose Folge)\r\n"
            "- pruefen.py: prüft offline, dass keine Datei verändert wurde und die Kette vollständig ist\r\n"
            "- index.html: durchsuchbare Übersicht im Browser\r\n\r\n"
            "Für eine Datenträgerüberlassung an die Finanzverwaltung (§ 147 Abs. 6 AO) zusätzlich den GoBD-Export\r\n"
            "der Buchhaltung (Buchhaltung → GoBD-Export) beilegen.\r\n"))
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="archive.exported", entity_type="archive", entity_id=None,
                       after={"year": year, "documents": count, "bytes": total})
    return path, f"archiv-{year or 'gesamt'}-{dt.datetime.now(dt.UTC):%Y%m%d}.zip"
