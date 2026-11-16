"use client";

import { RelDate } from "@/components/RelDate";
import { useFeedback } from "@/components/Feedback";
import { useCallback, useEffect, useMemo, useState, type DragEvent, type FormEvent } from "react";
import { Avatar, type Person } from "@/components/Avatar";
import { Icon } from "@/components/Icon";
import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Textarea } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface Project { id: string; name: string; color: string; task_count: number; done_count: number }
interface Task { id: string; column_id: string; title: string; description: string | null; assignee: string | null; assignee_id: string | null; assignee_name: string | null; assignee_color: string | null; assignee_photo: boolean; due_date: string | null; priority: "niedrig" | "mittel" | "hoch" | "dringend"; completed_at: string | null }
interface Column { id: string; name: string; is_done: boolean; tasks: Task[] }
interface Board { project: { id: string; name: string; color: string }; columns: Column[] }
interface SearchHit { projects: { id: string; name: string; color: string }[]; tasks: { id: string; title: string; priority: Task["priority"]; due_date: string | null; completed_at: string | null; project_id: string; project_name: string; project_color: string; column_name: string; assignee_name: string | null }[] }

const PRIO_TONE = { niedrig: "slate", mittel: "blue", hoch: "amber", dringend: "red" } as const;

export default function ProjectsPage() {
  const fb = useFeedback();
  const [projects, setProjects] = useState<Project[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [newProject, setNewProject] = useState("");
  const [draft, setDraft] = useState<{ column_id: string; title: string } | null>(null);
  const [edit, setEdit] = useState<Task | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit | null>(null);
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(true);
  useEffect(() => { try { if (localStorage.getItem("projekte.rail") === "closed") setRailOpen(false); } catch { /* ohne Speicher */ } }, []);
  const toggleRail = () => setRailOpen((o) => { try { localStorage.setItem("projekte.rail", o ? "closed" : "open"); } catch { /* ohne Speicher */ } return !o; });

  const loadProjects = useCallback(() => api<Project[]>("GET", "/api/projects").then((p) => {
    setProjects(p);
    setActive((a) => (a && p.some((x) => x.id === a) ? a : p[0]?.id ?? null));
  }), []);
  const loadBoard = useCallback(() => {
    if (!active) { setBoard(null); return; }
    void api<Board>("GET", `/api/projects/${active}/board`).then(setBoard).catch(() => setBoard(null));
  }, [active]);
  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { void api<{ shareholders: Person[] }>("GET", "/api/team").then((t) => setPeople(t.shareholders)); }, []);
  useEffect(loadBoard, [loadBoard]);

  // Suche über alle Projekte und Aufgaben (serverseitig, leicht verzögert)
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setHits(null); return; }
    const t = setTimeout(() => { void api<SearchHit>("GET", `/api/projects/search?q=${encodeURIComponent(q)}`).then(setHits).catch(() => setHits(null)); }, 200);
    return () => clearTimeout(t);
  }, [query]);

  // Treffer aus der Suche öffnen, sobald das Board des Projekts geladen ist
  useEffect(() => {
    if (!openTaskId || !board) return;
    const task = board.columns.flatMap((c) => c.tasks).find((t) => t.id === openTaskId);
    if (task) { setEdit(task); setOpenTaskId(null); }
  }, [board, openTaskId]);

  async function act(fn: () => Promise<void>) {
    setError(null);
    try { await fn(); loadBoard(); void loadProjects(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); }
  }

  const createProject = (e: FormEvent) => { e.preventDefault(); void act(async () => { const r = await api<{ id: string }>("POST", "/api/projects", { name: newProject }); setNewProject(""); setActive(r.id); }); };
  const deleteProject = (p: Project) => void act(async () => {
    if (!await fb.confirm(`Projekt „${p.name}“ mit ${p.task_count} Aufgabe(n) löschen?`)) return;
    await api("DELETE", `/api/projects/${p.id}`);
    if (active === p.id) setActive(null);
  });
  const createTask = (e: FormEvent) => { e.preventDefault(); if (!draft || !active) return; void act(async () => { await api("POST", `/api/projects/${active}/tasks`, draft); setDraft(null); }); };
  const saveTask = (e: FormEvent) => { e.preventDefault(); if (!edit) return; void act(async () => { const { id, title, description, assignee_id, due_date, priority } = edit; await api("PUT", `/api/tasks/${id}`, { title, description, assignee_id, due_date, priority }); setEdit(null); }); };
  const deleteTask = () => edit && act(async () => { if (!await fb.confirm("Aufgabe löschen?")) return; await api("DELETE", `/api/tasks/${edit.id}`); setEdit(null); });

  function onDrop(e: DragEvent, columnId: string, beforeId: string | null) {
    e.preventDefault();
    e.stopPropagation();
    const id = e.dataTransfer.getData("text/task") || dragging;
    setDragging(null);
    if (!id || id === beforeId) return;
    void act(async () => { await api("POST", `/api/tasks/${id}/move`, { column_id: columnId, before_id: beforeId }); });
  }

  const needle = query.trim().toLowerCase();
  const matches = (t: Task) => needle.length < 2 || [t.title, t.description, t.assignee_name].some((v) => v?.toLowerCase().includes(needle));
  const visibleProjects = useMemo(() => (needle.length < 2 ? projects
    : projects.filter((p) => p.name.toLowerCase().includes(needle) || hits?.tasks.some((t) => t.project_id === p.id) || hits?.projects.some((x) => x.id === p.id))), [projects, needle, hits]);
  const current = projects.find((p) => p.id === active);

  return (
    <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
      {/* ───────── Projektliste ───────── */}
      <aside className={`w-full shrink-0 flex-col gap-3 lg:sticky lg:top-20 lg:w-56 ${railOpen ? "flex" : "flex lg:hidden"}`}>
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">Projekte</h1>
          <p className="mt-0.5 text-[13px] text-slate-500 dark:text-slate-400">{projects.length} aktiv · Karten per Drag &amp; Drop verschieben</p>
        </div>
        <label className="relative block">
          <span className="sr-only">Projekte und Aufgaben durchsuchen</span>
          <Icon name="search" className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-slate-400" />
          <Input type="search" placeholder="Suchen …" title="Projekte und Aufgaben durchsuchen" value={query} onChange={(e) => setQuery(e.target.value)} className="pl-8" />
        </label>

        {hits && hits.tasks.length > 0 && (
          <div className="rounded-xl bg-white p-1.5 shadow-card dark:bg-slate-900">
            <p className="px-2 pb-1 pt-1 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">Aufgaben ({hits.tasks.length})</p>
            <ul className="max-h-72 overflow-y-auto">
              {hits.tasks.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => { setActive(t.project_id); setOpenTaskId(t.id); }}
                    className="w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-50 dark:hover:bg-white/5">
                    <span className={`block truncate ${t.completed_at ? "text-slate-400 line-through" : ""}`}>{t.title}</span>
                    <span className="flex items-center gap-1.5 text-xs text-slate-500">
                      <span className="size-2 shrink-0 rounded-full" style={{ background: t.project_color }} />
                      <span className="truncate">{t.project_name} · {t.column_name}{t.assignee_name ? ` · ${t.assignee_name}` : ""}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {hits && hits.tasks.length === 0 && hits.projects.length === 0 && <p className="px-1 text-sm text-slate-500">Keine Treffer für „{query.trim()}“.</p>}

        <nav aria-label="Projektliste" className="rounded-xl bg-white p-1.5 shadow-card dark:bg-slate-900">
          {visibleProjects.length === 0 && <p className="px-2 py-3 text-sm text-slate-500">{!projects.length ? "Noch keine Projekte." : needle.length >= 2 && !hits ? "Suche …" : "Kein Projekt passt zur Suche."}</p>}
          <ul className="space-y-0.5">
            {visibleProjects.map((p) => {
              const pct = p.task_count ? Math.round((p.done_count / p.task_count) * 100) : 0;
              return (
                <li key={p.id} className="group relative">
                  <button type="button" onClick={() => setActive(p.id)} aria-current={active === p.id ? "true" : undefined}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 pr-9 text-left text-sm transition ${active === p.id ? "bg-brand-50 text-brand-900 dark:bg-brand-500/15 dark:text-white" : "hover:bg-slate-50 dark:hover:bg-white/5"}`}>
                    <span className="size-2.5 shrink-0 rounded-full" style={{ background: p.color }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{p.name}</span>
                      <span className="mt-1 flex items-center gap-2">
                        <span className="h-1 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10"><span className="block h-full rounded-full" style={{ width: `${pct}%`, background: p.color }} /></span>
                        <span className="shrink-0 text-[11px] tabular text-slate-500">{p.done_count}/{p.task_count}</span>
                      </span>
                    </span>
                  </button>
                  <button type="button" aria-label={`Projekt ${p.name} löschen`} title="Projekt löschen" onClick={() => deleteProject(p)}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-slate-400 opacity-0 transition hover:bg-red-50 hover:text-red-600 focus:opacity-100 group-hover:opacity-100 dark:hover:bg-red-500/10">
                    <Icon name="x" className="size-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
          <form onSubmit={createProject} className="mt-1.5 flex gap-1.5 border-t border-slate-100 p-1.5 pt-2.5 dark:border-white/5">
            <Input aria-label="Neues Projekt" placeholder="Neues Projekt …" value={newProject} onChange={(e) => setNewProject(e.target.value)} className="flex-1" />
            <Button type="submit" variant="primary" disabled={!newProject.trim()} aria-label="Projekt anlegen">＋</Button>
          </form>
        </nav>
      </aside>

      {/* ───────── Kanban-Board ───────── */}
      <div className="min-w-0 flex-1">
        {error && <div className="mb-4"><Alert>{error}</Alert></div>}
        {!board ? <Card><Empty>{projects.length ? "Projekt links auswählen." : "Lege links ein Projekt an, um zu starten."}</Empty></Card> : (
          <>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="flex min-w-0 items-center gap-2.5 text-lg font-semibold tracking-tight">
                <button type="button" onClick={toggleRail} aria-label={railOpen ? "Projektliste einklappen" : "Projektliste ausklappen"} title={railOpen ? "Projektliste einklappen" : "Projektliste ausklappen"}
                  className="hidden size-8 shrink-0 place-items-center rounded-lg text-slate-500 shadow-card hover:text-slate-900 lg:grid dark:hover:text-white">
                  <Icon name="chevron" className={`size-4 transition ${railOpen ? "rotate-180" : ""}`} />
                </button>
                <span className="size-3 shrink-0 rounded-full" style={{ background: board.project.color }} />
                <span className="truncate">{board.project.name}</span>
              </h2>
              {needle.length >= 2 && <Badge tone="blue">Filter: „{query.trim()}“</Badge>}
              {current && <Button variant="ghost" className="!h-8 text-red-600 hover:!bg-red-50 hover:!text-red-700 dark:hover:!bg-red-500/10" onClick={() => deleteProject(current)}>Projekt löschen</Button>}
            </div>
            <div className="overflow-x-auto pb-4">
              <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${board.columns.length}, minmax(150px, 1fr))` }}>
                {board.columns.map((col) => {
                  const tasks = col.tasks.filter(matches);
                  return (
                    <section key={col.id} aria-label={col.name} className="flex min-w-0 flex-col rounded-xl bg-slate-100/80 p-2 dark:bg-white/[0.03]" onDragOver={(e) => e.preventDefault()} onDrop={(e) => onDrop(e, col.id, null)}>
                      <header className="mb-2 flex items-center justify-between gap-2 px-1.5 pt-0.5 text-[13px] font-semibold">
                        <span className="truncate">{col.name} <span className="ml-1 font-normal text-slate-400">{tasks.length}</span></span>
                        <button aria-label={`Aufgabe in ${col.name} anlegen`} className="grid size-6 shrink-0 place-items-center rounded-md text-slate-500 hover:bg-white hover:text-brand-600 dark:hover:bg-white/10" onClick={() => setDraft({ column_id: col.id, title: "" })}>＋</button>
                      </header>
                      <div className="flex min-h-16 flex-col gap-2">
                        {tasks.map((t) => (
                          <article
                            key={t.id}
                            draggable
                            onDragStart={(e) => { e.dataTransfer.setData("text/task", t.id); setDragging(t.id); }}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={(e) => onDrop(e, col.id, t.id)}
                            onClick={() => setEdit(t)}
                            className={`cursor-grab rounded-lg bg-white p-2.5 text-sm shadow-card transition hover:shadow-raised dark:bg-slate-900 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.07)] ${dragging === t.id ? "opacity-50" : ""}`}
                          >
                            <p className={`hyphens-auto break-words leading-snug ${t.completed_at ? "text-slate-400 line-through" : ""}`}>{t.title}</p>
                            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                              <Badge tone={PRIO_TONE[t.priority]}>{t.priority}</Badge>
                              {t.due_date && <RelDate date={t.due_date} mode={t.completed_at ? "relative" : "due"} />}
                              <span className="ml-auto"><Avatar person={t.assignee_id ? { id: t.assignee_id, name: t.assignee_name ?? "", color: t.assignee_color, has_photo: t.assignee_photo } : null} size={22} /></span>
                            </div>
                          </article>
                        ))}
                        {draft?.column_id === col.id && (
                          <form onSubmit={createTask} className="rounded-lg bg-white p-2 shadow-card dark:bg-slate-900">
                            <Input autoFocus aria-label="Titel der Aufgabe" placeholder="Titel …" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
                            <div className="mt-2 flex flex-wrap gap-2"><Button type="submit" variant="primary" disabled={!draft.title.trim()}>Hinzufügen</Button><Button onClick={() => setDraft(null)}>Abbrechen</Button></div>
                          </form>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>

      {edit && (
        <div role="dialog" aria-modal="true" aria-label="Aufgabe bearbeiten" className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={() => setEdit(null)}>
          <form onSubmit={saveTask} onClick={(e) => e.stopPropagation()} className="w-full max-w-lg space-y-3 rounded-xl bg-white p-5 dark:bg-slate-900">
            <Field label="Titel"><Input required value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></Field>
            <Field label="Beschreibung"><Textarea rows={5} value={edit.description ?? ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
            <div className="grid gap-2 sm:grid-cols-3">
              <Field label="Zuständig"><Select value={edit.assignee_id ?? ""} onChange={(e) => setEdit({ ...edit, assignee_id: e.target.value || null })}><option value="">– niemand –</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
              <Field label="Fällig"><Input type="date" value={edit.due_date ?? ""} onChange={(e) => setEdit({ ...edit, due_date: e.target.value || null })} /></Field>
              <Field label="Priorität"><Select value={edit.priority} onChange={(e) => setEdit({ ...edit, priority: e.target.value as Task["priority"] })}>{(["niedrig", "mittel", "hoch", "dringend"] as const).map((p) => <option key={p} value={p}>{p}</option>)}</Select></Field>
            </div>
            <div className="flex justify-between"><Button variant="danger" onClick={deleteTask}>Löschen</Button><div className="flex gap-2"><Button onClick={() => setEdit(null)}>Abbrechen</Button><Button type="submit" variant="primary">Speichern</Button></div></div>
          </form>
        </div>
      )}
    </div>
  );
}
