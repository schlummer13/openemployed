"""Projekte und Aufgaben (Kanban). Keine Rollen – jeder angemeldete Benutzer sieht und bearbeitet alles."""

from __future__ import annotations

import itertools
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import NotFoundError

DEFAULT_COLUMNS = [("Backlog", False), ("Zu erledigen", False), ("In Arbeit", False), ("Review", False), ("Erledigt", True)]


async def list_projects(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        "SELECT p.id, p.name, p.description, p.customer_id, p.color, p.hourly_rate, "
        "count(t.id) AS task_count, count(t.id) FILTER (WHERE t.completed_at IS NOT NULL) AS done_count "
        "FROM projects p LEFT JOIN tasks t ON t.project_id = p.id WHERE p.tenant_id = :t AND p.archived_at IS NULL "
        "GROUP BY p.id ORDER BY p.created_at"), {"t": tenant_id})
    return [dict(r) for r in result.mappings()]


async def create_project(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str) -> UUID:
    pid: UUID = await conn.scalar(text(
        "INSERT INTO projects (tenant_id, name, description, customer_id, color, created_by) "
        "VALUES (:t, :name, :description, :customer_id, :color, :a) RETURNING id"), data | {"t": tenant_id, "a": actor})
    for pos, (name, done) in enumerate(DEFAULT_COLUMNS):
        await conn.execute(text("INSERT INTO project_columns (project_id, name, position, is_done) VALUES (:p, :n, :pos, :d)"),
                           {"p": pid, "n": name, "pos": pos, "d": done})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="project.created", entity_type="project", entity_id=pid,
                       after=data)
    return pid


async def delete_project(conn: AsyncConnection, tenant_id: UUID, project_id: UUID, actor: str) -> None:
    """Entfernt ein Projekt aus allen Ansichten (inkl. seiner Aufgaben). Es wird archiviert statt gelöscht,
    damit das Änderungsprotokoll nachvollziehbar bleibt."""
    res = await conn.execute(text("UPDATE projects SET archived_at = now() WHERE tenant_id = :t AND id = :id AND archived_at IS NULL "
                                  "RETURNING name"), {"t": tenant_id, "id": project_id})
    name = res.scalar_one_or_none()
    if name is None:
        raise NotFoundError("Projekt nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="project.deleted", entity_type="project", entity_id=project_id,
                       before={"name": name})


async def search(conn: AsyncConnection, tenant_id: UUID, query: str, limit: int = 30) -> dict[str, Any]:
    """Volltextsuche über Projekte (Name, Beschreibung) und Aufgaben (Titel, Beschreibung, zuständige Person)."""
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    projects = (await conn.execute(text(
        "SELECT id, name, color FROM projects WHERE tenant_id = :t AND archived_at IS NULL "
        "AND (name ILIKE :q OR coalesce(description, '') ILIKE :q) ORDER BY name LIMIT :n"),
        {"t": tenant_id, "q": like, "n": limit})).mappings().all()
    tasks = (await conn.execute(text(
        "SELECT t.id, t.title, t.priority, t.due_date, t.completed_at, t.project_id, p.name AS project_name, p.color AS project_color, "
        "c.name AS column_name, s.name AS assignee_name FROM tasks t JOIN projects p ON p.id = t.project_id "
        "JOIN project_columns c ON c.id = t.column_id LEFT JOIN shareholders s ON s.id = t.assignee_id "
        "WHERE t.tenant_id = :t AND p.archived_at IS NULL AND (t.title ILIKE :q OR coalesce(t.description, '') ILIKE :q "
        "OR coalesce(s.name, '') ILIKE :q OR coalesce(t.assignee, '') ILIKE :q) "
        "ORDER BY t.completed_at IS NOT NULL, t.due_date NULLS LAST, t.title LIMIT :n"),
        {"t": tenant_id, "q": like, "n": limit})).mappings().all()
    return {"projects": [dict(r) for r in projects], "tasks": [dict(r) for r in tasks]}


async def board(conn: AsyncConnection, tenant_id: UUID, project_id: UUID) -> dict[str, Any]:
    project = (await conn.execute(text("SELECT id, name, description, customer_id, color FROM projects WHERE tenant_id = :t AND id = :id "
                                       "AND archived_at IS NULL"),
                                  {"t": tenant_id, "id": project_id})).mappings().one_or_none()
    if project is None:
        raise NotFoundError("Projekt nicht gefunden")
    cols = (await conn.execute(text("SELECT id, name, position, is_done FROM project_columns WHERE project_id = :p ORDER BY position"),
                               {"p": project_id})).mappings().all()
    tasks = (await conn.execute(text(
        "SELECT t.id, t.column_id, t.title, t.description, t.assignee, t.assignee_id, s.name AS assignee_name, "
        "s.color AS assignee_color, (s.photo IS NOT NULL) AS assignee_photo, t.due_date, t.priority, t.position, t.completed_at "
        "FROM tasks t LEFT JOIN shareholders s ON s.id = t.assignee_id WHERE t.project_id = :p ORDER BY t.position"),
        {"p": project_id})).mappings().all()
    return {"project": dict(project), "columns": [dict(c) | {"tasks": [dict(t) for t in tasks if t["column_id"] == c["id"]]}
                                                   for c in cols]}


async def _column(conn: AsyncConnection, project_id: UUID, column_id: UUID) -> dict[str, Any]:
    col = (await conn.execute(text("SELECT id, is_done FROM project_columns WHERE id = :c AND project_id = :p"),
                              {"c": column_id, "p": project_id})).mappings().one_or_none()
    if col is None:
        raise NotFoundError("Spalte nicht gefunden")
    return dict(col)


async def _check_assignee(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
    data = {"assignee_id": None} | data
    if data["assignee_id"] is not None:
        email = await conn.execute(text("SELECT email FROM shareholders WHERE tenant_id = :t AND id = :id"),
                                   {"t": tenant_id, "id": data["assignee_id"]})
        row = email.one_or_none()
        if row is None:
            raise NotFoundError("Gesellschafter nicht gefunden")
        data["assignee"] = row.email
    return data


async def create_task(conn: AsyncConnection, tenant_id: UUID, project_id: UUID, data: dict[str, Any], actor: str) -> UUID:
    await board(conn, tenant_id, project_id)
    data = await _check_assignee(conn, tenant_id, data)
    col = await _column(conn, project_id, data["column_id"])
    top = await conn.scalar(text("SELECT COALESCE(MAX(position), 0) FROM tasks WHERE column_id = :c"), {"c": data["column_id"]})
    tid: UUID = await conn.scalar(text(
        "INSERT INTO tasks (tenant_id, project_id, column_id, title, description, assignee, assignee_id, due_date, priority, position, "
        "completed_at, created_by) "
        "VALUES (:t, :p, :column_id, :title, :description, :assignee, :assignee_id, :due_date, :priority, :pos, "
        "CASE WHEN :done THEN now() ELSE NULL END, :a) RETURNING id"),
        data | {"t": tenant_id, "p": project_id, "pos": float(top) + 1024, "done": col["is_done"], "a": actor})
    return tid


async def update_task(conn: AsyncConnection, tenant_id: UUID, task_id: UUID, data: dict[str, Any]) -> None:
    data = await _check_assignee(conn, tenant_id, data)
    res = await conn.execute(text(
        "UPDATE tasks SET title = :title, description = :description, assignee = :assignee, assignee_id = :assignee_id, "
        "due_date = :due_date, "
        "priority = :priority, updated_at = now() WHERE tenant_id = :t AND id = :id"), data | {"t": tenant_id, "id": task_id})
    if res.rowcount == 0:
        raise NotFoundError("Aufgabe nicht gefunden")


async def move_task(conn: AsyncConnection, tenant_id: UUID, task_id: UUID, column_id: UUID, before_id: UUID | None) -> None:
    """Verschiebt eine Aufgabe in eine Spalte vor ``before_id`` (oder ans Ende). Position = Mitte der Nachbarn."""
    task = (await conn.execute(text("SELECT id, project_id FROM tasks WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                               {"t": tenant_id, "id": task_id})).mappings().one_or_none()
    if task is None:
        raise NotFoundError("Aufgabe nicht gefunden")
    col = await _column(conn, task["project_id"], column_id)
    positions = [float(r[0]) for r in await conn.execute(text(
        "SELECT position FROM tasks WHERE column_id = :c AND id <> :id ORDER BY position"), {"c": column_id, "id": task_id})]
    if before_id is None:
        pos = (positions[-1] if positions else 0) + 1024
    else:
        before = await conn.scalar(text("SELECT position FROM tasks WHERE id = :b AND column_id = :c"), {"b": before_id, "c": column_id})
        if before is None:
            raise NotFoundError("Zielposition nicht gefunden")
        prev = [p for p in positions if p < float(before)]
        pos = ((prev[-1] if prev else float(before) - 1024) + float(before)) / 2
    await conn.execute(text(
        "UPDATE tasks SET column_id = :c, position = :pos, "
        "completed_at = CASE WHEN :done THEN COALESCE(completed_at, now()) ELSE NULL END, "
        "updated_at = now() WHERE id = :id"), {"c": column_id, "pos": pos, "done": col["is_done"], "id": task_id})
    # Positionen bei zu kleinen Abständen neu verteilen (Gleitkomma-Präzision)
    ordered = sorted([*positions, pos])
    if len(ordered) > 1 and min(b - a for a, b in itertools.pairwise(ordered)) < 1e-6:
        ids = [r[0] for r in await conn.execute(text("SELECT id FROM tasks WHERE column_id = :c ORDER BY position"), {"c": column_id})]
        for i, tid in enumerate(ids, start=1):
            await conn.execute(text("UPDATE tasks SET position = :p WHERE id = :id"), {"p": i * 1024.0, "id": tid})


async def delete_task(conn: AsyncConnection, tenant_id: UUID, task_id: UUID, actor: str) -> None:
    res = await conn.execute(text("DELETE FROM tasks WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": task_id})
    if res.rowcount == 0:
        raise NotFoundError("Aufgabe nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="task.deleted", entity_type="task", entity_id=task_id)


PRIORITY_ORDER = {"dringend": 0, "hoch": 1, "mittel": 2, "niedrig": 3}


async def open_tasks(conn: AsyncConnection, tenant_id: UUID, limit: int = 12) -> list[dict[str, Any]]:
    """Offene Aufgaben über alle Projekte – sortiert nach Priorität und Fälligkeit (für die Übersicht)."""
    rows = await conn.execute(text(
        "SELECT t.id, t.title, t.priority, t.due_date, t.project_id, p.name AS project_name, p.color AS project_color, t.assignee_id, "
        "s.name AS assignee_name, s.color AS assignee_color, (s.photo IS NOT NULL) AS assignee_photo FROM tasks t "
        "JOIN projects p ON p.id = t.project_id LEFT JOIN shareholders s ON s.id = t.assignee_id "
        "WHERE t.tenant_id = :t AND t.completed_at IS NULL AND p.archived_at IS NULL "
        "ORDER BY CASE t.priority WHEN 'dringend' THEN 0 WHEN 'hoch' THEN 1 WHEN 'mittel' THEN 2 ELSE 3 END, t.due_date NULLS LAST, "
        "t.created_at LIMIT :n"), {"t": tenant_id, "n": limit})
    return [dict(r) for r in rows.mappings()]
