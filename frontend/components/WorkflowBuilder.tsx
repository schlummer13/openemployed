"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useFeedback } from "./Feedback";
import { Icon } from "./Icon";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Textarea } from "./ui";
import { api, HttpError } from "@/lib/http";

type StepType = "if" | "condition" | "email" | "task" | "http" | "python";
interface Step { id: string; type: StepType; config: Record<string, unknown> }
interface Workflow { id: string | null; name: string; description: string | null; enabled: boolean; trigger_type: "cron" | "event" | "manual"; trigger_value: string | null; steps: Step[]; last_run?: { status: string; queued_at: string } | null }
interface Run { id: string; workflow_id: string; workflow_name: string; trigger: string; status: string; log: { id: string; type: string; ok: boolean; result?: unknown; error?: string; ms?: number }[]; error: string | null; queued_at: string }
interface Meta { workflows: Workflow[]; runs: Run[]; events: Record<string, string>; step_types: Record<StepType, string>; ops: string[]; projects: { id: string; name: string }[] }

const STATUS: Record<string, [string, "green" | "amber" | "red" | "slate" | "blue"]> = {
  succeeded: ["erfolgreich", "green"], stopped: ["Bedingung nicht erfüllt", "slate"], failed: ["Fehler", "red"], queued: ["wartet", "blue"], running: ["läuft", "amber"],
};
const OP_LABEL: Record<string, string> = { "==": "ist gleich", "!=": "ist ungleich", ">": "größer als", ">=": "mindestens", "<": "kleiner als", "<=": "höchstens", contains: "enthält", not_contains: "enthält nicht", empty: "ist leer", not_empty: "ist nicht leer" };
const CRON_PRESETS: [string, string][] = [["0 7 * * *", "täglich 07:00"], ["0 8 * * 1", "montags 08:00"], ["0 9 1 * *", "am 1. des Monats 09:00"], ["0 * * * *", "stündlich"], ["*/15 * * * *", "alle 15 Minuten"]];
const ICON: Record<StepType, string> = { if: "⑂", condition: "?", email: "@", task: "✓", http: "↗", python: "py" };
const TEMPLATES: { name: string; wf: Omit<Workflow, "id"> }[] = [
  { name: "Große Rechnung → Mail an die Geschäftsführung", wf: { name: "Große Rechnung melden", description: null, enabled: false, trigger_type: "event", trigger_value: "invoice.issued", steps: [
    { id: "gross", type: "condition", config: { left: "{{data.total_gross}}", op: ">=", right: "5000" } },
    { id: "mail", type: "email", config: { to: "gf@firma.de", subject: "Rechnung {{data.number}} über {{data.total_gross}} €", body: "Neue große Rechnung an {{data.buyer.name}}." } }] } },
  { name: "Angebot angenommen → Projektaufgabe", wf: { name: "Auftrag starten", description: null, enabled: false, trigger_type: "event", trigger_value: "offer.accepted", steps: [
    { id: "aufgabe", type: "task", config: { project_id: "", title: "Kickoff {{data.number}}", due_in_days: "2" } }] } },
  { name: "Montags: offene Posten an Slack/n8n", wf: { name: "Wochenbericht senden", description: null, enabled: false, trigger_type: "cron", trigger_value: "0 8 * * 1", steps: [
    { id: "senden", type: "http", config: { method: "POST", url: "https://hooks.slack.com/services/…", body: { text: "Neue Woche – bitte offene Posten prüfen." } } }] } },
  { name: "Rechnung: groß → GF, sonst Buchhaltung (Abzweigung)", wf: { name: "Rechnung verteilen", description: null, enabled: false, trigger_type: "event", trigger_value: "invoice.issued", steps: [
    { id: "gross", type: "if", config: { left: "{{data.total_gross}}", op: ">=", right: "5000",
      then: [{ id: "an_gf", type: "email", config: { to: "gf@firma.de", subject: "Große Rechnung {{data.number}}", body: "Betrag {{data.total_gross}} €" } }],
      else: [{ id: "an_bh", type: "email", config: { to: "buchhaltung@firma.de", subject: "Rechnung {{data.number}}", body: "Zur Info." } }] } }] } },
  { name: "Beleg gebucht → Python-Prüfung", wf: { name: "Beleg prüfen", description: null, enabled: false, trigger_type: "event", trigger_value: "receipt.booked", steps: [
    { id: "pruefen", type: "python", config: { code: "betrag = float(data['data'].get('gross_amount') or 0)\nresult = {'auffaellig': betrag > 1000, 'betrag': betrag}" } },
    { id: "nur_auffaellige", type: "condition", config: { left: "{{steps.pruefen.result.auffaellig}}", op: "==", right: "True" } },
    { id: "mail", type: "email", config: { to: "buchhaltung@firma.de", subject: "Auffälliger Beleg", body: "Betrag {{steps.pruefen.result.betrag}} €" } }] } },
];

const OUTPUTS: Record<StepType, string[]> = { if: ["passed", "branch"], condition: ["passed"], email: ["message_id", "to"], task: ["task_id"],
  http: ["status", "body"], python: ["result", "stdout"] };
const FieldCtx = createContext<{ paths: string[] }>({ paths: [] });

function flatSteps(steps: Step[]): Step[] {
  return steps.flatMap((s) => [s, ...(s.type === "if" ? [...flatSteps((s.config.then as Step[]) ?? []), ...flatSteps((s.config.else as Step[]) ?? [])] : [])]);
}

/** Eingabefeld mit durchsuchbarer Auswahl der verfügbaren Felder (Platzhalter) – freie Eingabe bleibt möglich. */
function FieldPicker({ value, onChange, placeholder, label }: { value: string; onChange: (v: string) => void; placeholder?: string; label: string }) {
  const { paths } = useContext(FieldCtx);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const term = (q || "").toLowerCase();
  const hits = paths.filter((p) => p.toLowerCase().includes(term)).slice(0, 60);
  const group = (p: string) => (p.startsWith("data.") ? "Ereignisdaten" : p.startsWith("steps.") ? "Vorherige Blöcke" : "Allgemein");
  return (
    <div ref={ref} className="relative min-w-0">
      <div className="flex">
        <Input aria-label={label} placeholder={placeholder ?? "Feld wählen oder Wert eingeben"} className="!rounded-r-none font-mono text-xs" value={value}
          onFocus={() => { setQ(""); setOpen(true); }} onChange={(e) => { onChange(e.target.value); setQ(e.target.value.replace(/[{}]/g, "")); setOpen(true); }} />
        <button type="button" aria-label="Felder anzeigen" onClick={() => { setQ(""); setOpen(!open); }} className="rounded-r-lg border border-l-0 border-slate-200 px-2 text-slate-500 hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/5"><Icon name="chevron" className="size-3.5 rotate-90" /></button>
      </div>
      {open && hits.length > 0 && (
        <ul className="absolute inset-x-0 top-10 z-50 max-h-64 overflow-y-auto rounded-xl bg-white p-1 text-xs shadow-pop dark:bg-slate-900" role="listbox">
          {hits.map((p, i) => (
            <li key={p}>
              {(i === 0 || group(hits[i - 1]!) !== group(p)) && <p className="px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{group(p)}</p>}
              <button type="button" role="option" aria-selected={value === `{{${p}}}`} onClick={() => { onChange(`{{${p}}}`); setOpen(false); }}
                className="block w-full truncate rounded-md px-2 py-1.5 text-left font-mono hover:bg-brand-50 dark:hover:bg-brand-500/10">{p}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function HeaderEditor({ value, onChange }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  // lokaler Entwurf: leere Zeilen bleiben beim Tippen erhalten, gespeichert werden nur Header mit Namen
  const [draft, setDraft] = useState<[string, string][]>(() => Object.entries(value ?? {}));
  const update = (list: [string, string][]) => { setDraft(list); onChange(Object.fromEntries(list.filter(([k]) => k.trim()))); };
  return (
    <div className="grid gap-1.5">
      <p className="text-[11px] font-medium text-slate-500">Kopfzeilen (Header)</p>
      {draft.map(([k, v], i) => (
        <div key={i} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] gap-1.5">
          <Input aria-label="Header-Name" placeholder="z. B. Authorization" className="font-mono text-xs" value={k} onChange={(e) => update(draft.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <Input aria-label="Header-Wert" placeholder="Bearer … oder {{data.…}}" className="font-mono text-xs" value={v} onChange={(e) => update(draft.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <button type="button" aria-label="Header entfernen" onClick={() => update(draft.filter((_, j) => j !== i))} className="px-1 text-slate-400 hover:text-red-600"><Icon name="x" className="size-4" /></button>
        </div>
      ))}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => update([...draft, ["", ""]])} className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-medium hover:bg-slate-200 dark:bg-white/5">+ Header</button>
        {[["Authorization", "Bearer "], ["Content-Type", "application/json"], ["X-API-Key", ""]].map(([k, v]) => !draft.some((r) => r[0].toLowerCase() === k!.toLowerCase()) && (
          <button key={k} type="button" onClick={() => update([...draft, [k!, v!]])} className="rounded-full bg-slate-100 px-2.5 py-0.5 font-mono text-[11px] hover:bg-slate-200 dark:bg-white/5">{k}</button>
        ))}
      </div>
    </div>
  );
}

function newStep(type: StepType): Step {
  const id = `${type}_${Math.random().toString(36).slice(2, 6)}`;
  const config: Record<StepType, Record<string, unknown>> = {
    if: { left: "", op: ">=", right: "", then: [], else: [] },
    condition: { left: "", op: "==", right: "" }, email: { to: "", subject: "", body: "" }, task: { project_id: "", title: "", due_in_days: "" },
    http: { method: "POST", url: "https://", headers: {}, body: "" }, python: { code: "# Eingabe: data (dict) – Ergebnis in result speichern\nresult = {'ok': True}" },
  };
  return { id, type, config: config[type] };
}

export function WorkflowBuilder() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [edit, setEdit] = useState<Workflow | null>(null);
  const [testData, setTestData] = useState("{}");
  const [lastRun, setLastRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [eventPaths, setEventPaths] = useState<string[]>([]);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<Meta>("GET", "/api/workflows").then(setMeta); }, []);
  const triggerKey = edit ? `${edit.trigger_type}:${edit.trigger_value ?? ""}` : "";
  useEffect(() => {
    if (!edit) return;
    const ev = edit.trigger_type === "event" && edit.trigger_value ? `?event=${encodeURIComponent(edit.trigger_value)}` : "";
    void api<{ paths: string[] }>("GET", `/api/workflows/fields${ev}`).then((r) => setEventPaths(r.paths)).catch(() => setEventPaths([]));
  }, [triggerKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const fieldCtx = useMemo(() => ({ paths: [...eventPaths, ...flatSteps(edit?.steps ?? []).flatMap((st) => OUTPUTS[st.type].map((o) => `steps.${st.id}.${o}`))] }), [eventPaths, edit?.steps]);
  useEffect(load, [load]);
  if (!meta) return <p className="text-sm text-slate-500">Lade …</p>;

  const body = (w: Workflow) => ({ name: w.name, description: w.description, enabled: w.enabled, trigger_type: w.trigger_type, trigger_value: w.trigger_type === "manual" ? null : w.trigger_value, steps: w.steps });
  const save = async (w: Workflow): Promise<string | null> => {
    setBusy(true);
    try {
      let id = w.id;
      if (id) await api("PUT", `/api/workflows/${id}`, body(w));
      else id = (await api<{ id: string }>("POST", "/api/workflows", body(w))).id;
      toast("Workflow gespeichert."); setEdit({ ...w, id }); load(); return id;
    } catch (e) { toast(e instanceof HttpError ? e.message : "Speichern fehlgeschlagen", "error"); return null; } finally { setBusy(false); }
  };
  const test = async () => {
    if (!edit) return;
    let data: unknown;
    try { data = JSON.parse(testData || "{}"); } catch { toast("Testdaten sind kein gültiges JSON.", "error"); return; }
    const id = await save(edit);
    if (!id) return;
    setBusy(true);
    try { const r = await api<{ status: string; run: Run }>("POST", `/api/workflows/${id}/run`, { data }); setLastRun(r.run); load(); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Testlauf fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const triggerLabel = (w: Workflow) => (w.trigger_type === "event" ? `bei „${meta.events[w.trigger_value ?? ""] ?? w.trigger_value}“` : w.trigger_type === "cron" ? (CRON_PRESETS.find((p) => p[0] === w.trigger_value)?.[1] ?? `Cron ${w.trigger_value}`) : "manuell");

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader title="Eigene Workflows" description="Auslöser wählen (Zeitplan, Ereignis oder manuell) und Blöcke verketten: Bedingungen, E-Mails, Aufgaben, HTTP-Aufrufe (z. B. Slack, n8n) oder eigener Python-Code in einer abgeschotteten Sandbox."
          actions={<Button variant="primary" onClick={() => { setLastRun(null); setTestData("{}"); setEdit({ id: null, name: "Neuer Workflow", description: null, enabled: false, trigger_type: "event", trigger_value: Object.keys(meta.events)[0] ?? null, steps: [] }); }}>+ Workflow</Button>} />
        {meta.workflows.length === 0 ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {TEMPLATES.map((t) => <button key={t.name} type="button" onClick={() => { setLastRun(null); setEdit({ ...t.wf, id: null }); }} className="rounded-xl border border-dashed border-slate-300 p-4 text-left text-sm hover:border-brand-400 hover:bg-brand-50/40 dark:border-white/10 dark:hover:bg-white/[0.03]"><span className="font-medium">{t.name}</span><span className="block text-xs text-slate-500">Vorlage übernehmen</span></button>)}
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-white/5">
            {meta.workflows.map((w) => (
              <li key={w.id} className="flex flex-wrap items-center gap-3 py-3">
                <span className={`size-2 rounded-full ${w.enabled ? "bg-emerald-500" : "bg-slate-300"}`} />
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => { setLastRun(null); setTestData("{}"); setEdit(w); }}>
                  <span className="block font-medium hover:underline">{w.name}</span>
                  <span className="block text-xs text-slate-500">{triggerLabel(w)} · {w.steps.length} Blöcke</span>
                </button>
                {w.last_run && <Badge tone={STATUS[w.last_run.status]?.[1] ?? "slate"}>{STATUS[w.last_run.status]?.[0] ?? w.last_run.status}</Badge>}
              </li>
            ))}
          </ul>
        )}
        {meta.workflows.length > 0 && <p className="mt-3 text-xs text-slate-500">Vorlagen: {TEMPLATES.map((t, i) => <button key={t.name} type="button" className="text-brand-600 hover:underline dark:text-brand-300" onClick={() => { setLastRun(null); setEdit({ ...t.wf, id: null }); }}>{t.name}{i < TEMPLATES.length - 1 ? " · " : ""}</button>)}</p>}
      </Card>

      {meta.runs.length > 0 && (
        <Card>
          <CardHeader title="Letzte Läufe" />
          <ul className="divide-y divide-slate-100 text-sm dark:divide-white/5">
            {meta.runs.slice(0, 15).map((r) => <li key={r.id} className="flex flex-wrap items-center gap-3 py-2"><span className="w-36 text-xs tabular text-slate-500">{new Date(r.queued_at).toLocaleString("de-DE")}</span><span className="min-w-0 flex-1 truncate">{r.workflow_name}<span className="block text-xs text-slate-500">{r.error ?? r.trigger}</span></span><Badge tone={STATUS[r.status]?.[1] ?? "slate"}>{STATUS[r.status]?.[0] ?? r.status}</Badge></li>)}
          </ul>
        </Card>
      )}

      <Drawer open={!!edit} onClose={() => setEdit(null)} size="lg" title={edit?.name ?? ""} subtitle="Auslöser und Blöcke werden von oben nach unten ausgeführt"
        footer={edit && <>
          {edit.id && <Button variant="ghost" className="mr-auto" onClick={async () => { if (await confirm(`Workflow „${edit.name}“ löschen?\n\nDas Protokoll wird mit gelöscht.`)) { await api("DELETE", `/api/workflows/${edit.id}`); setEdit(null); load(); } }}>Löschen</Button>}
          <Button disabled={busy} onClick={() => void test()}>Speichern & testen</Button>
          <Button variant="primary" disabled={busy} onClick={() => void save(edit)}>Speichern</Button>
        </>}>
        {edit && (
          <div className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
              <Field label="Name"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} />aktiv</label>
            </div>

            <div className="rounded-xl border-2 border-brand-200 bg-brand-50/50 p-4 dark:border-brand-500/30 dark:bg-brand-500/5">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-brand-700 dark:text-brand-300">Auslöser</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Select aria-label="Auslöser" value={edit.trigger_type} onChange={(e) => { const t = e.target.value as Workflow["trigger_type"]; setEdit({ ...edit, trigger_type: t, trigger_value: t === "cron" ? "0 7 * * *" : t === "event" ? Object.keys(meta.events)[0] ?? null : null }); }}>
                  <option value="event">Ereignis</option><option value="cron">Zeitplan</option><option value="manual">manuell</option>
                </Select>
                {edit.trigger_type === "event" && <Select aria-label="Ereignis" value={edit.trigger_value ?? ""} onChange={(e) => setEdit({ ...edit, trigger_value: e.target.value })}>{Object.entries(meta.events).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>}
                {edit.trigger_type === "cron" && <div className="flex gap-2"><Select aria-label="Vorlage" value={CRON_PRESETS.some((p) => p[0] === edit.trigger_value) ? edit.trigger_value ?? "" : ""} onChange={(e) => e.target.value && setEdit({ ...edit, trigger_value: e.target.value })} className="!w-40"><option value="">eigener …</option>{CRON_PRESETS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</Select><Input aria-label="Cron-Ausdruck" className="font-mono" value={edit.trigger_value ?? ""} onChange={(e) => setEdit({ ...edit, trigger_value: e.target.value })} /></div>}
              </div>
              <p className="mt-2 text-xs text-slate-500">Platzhalter: <code>{"{{data.feld}}"}</code> (Daten des Ereignisses), <code>{"{{steps.kennung.…}}"}</code> (Ergebnis eines vorherigen Blocks), <code>{"{{now}}"}</code>.</p>
            </div>

            <FieldCtx.Provider value={fieldCtx}>
              <StepList steps={edit.steps} onChange={(steps) => setEdit({ ...edit, steps })} meta={meta} lastRun={lastRun} depth={1} />
            </FieldCtx.Provider>

            <Field label="Testdaten (JSON, erscheinen als data)" hint="z. B. {&quot;number&quot;: &quot;RE-2026-0001&quot;, &quot;total_gross&quot;: &quot;7140.00&quot;}">
              <Textarea rows={3} className="font-mono text-xs" value={testData} onChange={(e) => setTestData(e.target.value)} />
            </Field>
            {lastRun && <Alert kind={lastRun.status === "failed" ? "error" : lastRun.status === "succeeded" ? "success" : "info"}>Testlauf: {STATUS[lastRun.status]?.[0] ?? lastRun.status}{lastRun.error ? ` – ${lastRun.error}` : ""}</Alert>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

function StepList({ steps, onChange, meta, lastRun, depth }: { steps: Step[]; onChange: (s: Step[]) => void; meta: Meta; lastRun: Run | null; depth: number }) {
  const types = Object.fromEntries(Object.entries(meta.step_types).filter(([k]) => k !== "if" || depth < 3)) as Record<StepType, string>;
  const insert = (i: number, t: StepType) => onChange([...steps.slice(0, i), newStep(t), ...steps.slice(i)]);
  const update = (i: number, s: Step) => onChange(steps.map((x, j) => (j === i ? s : x)));
  const move = (i: number, d: number) => { const st = [...steps]; const [x] = st.splice(i, 1); st.splice(i + d, 0, x!); onChange(st); };
  return (
    <div className="grid min-w-0 gap-0">
      <AddButton types={types} onAdd={(t) => insert(0, t)} />
      {steps.map((s, i) => {
        const log = lastRun?.log.find((l) => l.id === s.id);
        const c = s.config as Record<string, unknown>;
        return (
          <div key={s.id + i}>
            <div className="mx-auto h-3 w-px bg-slate-300 dark:bg-white/20" />
            <div className={`min-w-0 rounded-xl border ${depth > 1 ? "p-2.5" : "p-3 sm:p-4"} ${s.type === "if" ? "border-sky-200 bg-sky-50/50 dark:border-sky-500/30 dark:bg-sky-500/5" : "border-slate-200 bg-white dark:border-white/10 dark:bg-slate-900"}`}>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-slate-900 font-mono text-[11px] font-semibold text-white dark:bg-white dark:text-slate-900">{ICON[s.type]}</span>
                <span className="truncate font-medium">{meta.step_types[s.type]}</span>
                <Input aria-label="Kennung" value={s.id} onChange={(e) => update(i, { ...s, id: e.target.value.replace(/[^A-Za-z0-9_]/g, "") })} className="!h-7 !w-24 font-mono !text-xs" />
                <span className="ml-auto flex shrink-0 gap-1">
                  <button type="button" aria-label="nach oben" disabled={i === 0} onClick={() => move(i, -1)} className="rounded px-1.5 text-slate-400 hover:text-slate-700 disabled:opacity-30">↑</button>
                  <button type="button" aria-label="nach unten" disabled={i === steps.length - 1} onClick={() => move(i, 1)} className="rounded px-1.5 text-slate-400 hover:text-slate-700 disabled:opacity-30">↓</button>
                  <button type="button" aria-label="Block entfernen" onClick={() => onChange(steps.filter((_, j) => j !== i))} className="rounded px-1.5 text-slate-400 hover:text-red-600"><Icon name="x" className="size-4" /></button>
                </span>
              </div>
              <StepForm step={s} meta={meta} onChange={(p) => update(i, { ...s, config: { ...s.config, ...p } })} />
              {log && <pre className={`mt-3 max-h-40 overflow-auto rounded-lg px-3 py-2 text-[11px] ${log.ok ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-200" : "bg-red-50 text-red-800 dark:bg-red-500/10 dark:text-red-200"}`}>{log.ok ? JSON.stringify(log.result, null, 2) : log.error}</pre>}
            </div>
            {s.type === "if" && <Fork step={s} log={log} onChange={(b, sub) => update(i, { ...s, config: { ...s.config, [b]: sub } })} meta={meta} lastRun={lastRun} depth={depth} />}
            <div className="mx-auto h-3 w-px bg-slate-300 dark:bg-white/20" />
            <AddButton types={types} onAdd={(t) => insert(i + 1, t)} />
          </div>
        );
      })}
    </div>
  );
}

/** Gabelung: Linie teilt sich unter der Bedingung in „Ja“ (grün) und „Nein“ (rot) und läuft danach wieder zusammen. */
function Fork({ step, log, onChange, meta, lastRun, depth }: { step: Step; log?: { ok: boolean; result?: unknown }; onChange: (b: "then" | "else", s: Step[]) => void; meta: Meta; lastRun: Run | null; depth: number }) {
  const c = step.config as Record<string, unknown>;
  const taken = log?.ok ? ((log.result as { branch?: string } | undefined)?.branch === "Ja" ? "then" : "else") : null;
  const edge = "calc(25% - 0.375rem)";  // Mitte der beiden Spalten (gap-3)
  return (
    <div className={depth === 1 ? "-mx-1 overflow-x-auto px-1" : ""}>
      <div className={depth === 1 ? "min-w-[26rem]" : "min-w-[24rem]"}>
        <div className="mx-auto h-4 w-px bg-slate-300 dark:bg-white/20" />
        <div className="relative grid grid-cols-2 gap-3">
          {/* Verzweigung oben und Zusammenführung unten */}
          <div aria-hidden className="pointer-events-none absolute top-0 h-0.5" style={{ left: edge, right: edge }}>
            <div className="absolute inset-y-0 left-0 right-1/2 bg-emerald-400 dark:bg-emerald-500/70" /><div className="absolute inset-y-0 left-1/2 right-0 bg-red-400 dark:bg-red-500/70" />
          </div>
          <div aria-hidden className="pointer-events-none absolute bottom-0 h-0.5 bg-slate-300 dark:bg-white/20" style={{ left: edge, right: edge }} />
          {(["then", "else"] as const).map((b) => {
            const yes = b === "then";
            const dim = taken !== null && taken !== b;
            return (
              <div key={b} className={`flex min-w-0 flex-col items-stretch transition-opacity ${dim ? "opacity-40" : ""}`}>
                <div className={`mx-auto h-4 w-0.5 ${yes ? "bg-emerald-400 dark:bg-emerald-500/70" : "bg-red-400 dark:bg-red-500/70"}`} />
                <span className={`mx-auto rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${yes ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300"}`}>{yes ? "Ja" : "Nein"}{taken === b ? " ✓" : ""}</span>
                <div className={`mx-auto h-2 w-0.5 ${yes ? "bg-emerald-400 dark:bg-emerald-500/70" : "bg-red-400 dark:bg-red-500/70"}`} />
                <div className={`min-w-0 rounded-xl border-2 border-dashed p-1.5 ${yes ? "border-emerald-200 dark:border-emerald-500/25" : "border-red-200 dark:border-red-500/25"}`}>
                  <StepList steps={(c[b] as Step[] | undefined) ?? []} onChange={(sub) => onChange(b, sub)} meta={meta} lastRun={lastRun} depth={depth + 1} />
                  {((c[b] as Step[] | undefined) ?? []).length === 0 && <p className="mt-1 text-center text-[11px] text-slate-400">{yes ? "nichts tun" : "weiter ohne Aktion"}</p>}
                </div>
                <div className="mx-auto min-h-4 w-px flex-1 bg-slate-300 dark:bg-white/20" />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function AddButton({ types, onAdd }: { types: Record<StepType, string>; onAdd: (t: StepType) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col items-center">
      <button type="button" onClick={() => setOpen(!open)} className="grid size-7 place-items-center rounded-full border border-dashed border-slate-300 text-slate-500 hover:border-brand-500 hover:text-brand-600 dark:border-white/20" aria-label="Block hinzufügen">+</button>
      {open && <div className="mt-2 flex flex-wrap justify-center gap-1.5">{(Object.entries(types) as [StepType, string][]).map(([k, l]) => <button key={k} type="button" onClick={() => { onAdd(k); setOpen(false); }} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium hover:bg-brand-100 dark:bg-white/5 dark:hover:bg-brand-500/20">{l}</button>)}</div>}
    </div>
  );
}

function StepForm({ step, meta, onChange }: { step: Step; meta: Meta; onChange: (p: Record<string, unknown>) => void }) {
  const c = step.config as Record<string, string>;
  switch (step.type) {
    case "if":
    case "condition":
      return <div className="grid gap-2 sm:grid-cols-3">
        <FieldPicker label="Wert" value={c.left ?? ""} onChange={(v) => onChange({ left: v })} />
        <Select aria-label="Vergleich" value={c.op} onChange={(e) => onChange({ op: e.target.value })}>{meta.ops.map((o) => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}</Select>
        {!["empty", "not_empty"].includes(c.op ?? "") && <FieldPicker label="Vergleichswert" placeholder="Wert oder Feld" value={c.right ?? ""} onChange={(v) => onChange({ right: v })} />}
        <p className="text-xs text-slate-500 sm:col-span-3">{step.type === "if" ? "Je nach Ergebnis läuft der Zweig „Ja“ oder „Nein“, danach geht es unten weiter." : "Ist die Bedingung nicht erfüllt, endet der Lauf hier."}</p>
      </div>;
    case "email":
      return <div className="grid gap-2">
        <Input aria-label="An" placeholder="Empfänger (mehrere mit Komma)" value={c.to ?? ""} onChange={(e) => onChange({ to: e.target.value })} />
        <Input aria-label="Betreff" placeholder="Betreff" value={c.subject ?? ""} onChange={(e) => onChange({ subject: e.target.value })} />
        <Textarea aria-label="Text" rows={4} placeholder="Text" value={c.body ?? ""} onChange={(e) => onChange({ body: e.target.value })} />
      </div>;
    case "task":
      return <div className="grid gap-2 sm:grid-cols-2">
        <Select aria-label="Projekt" value={c.project_id ?? ""} onChange={(e) => onChange({ project_id: e.target.value })}><option value="">Projekt wählen …</option>{meta.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
        <Input aria-label="Fällig in Tagen" type="number" placeholder="fällig in … Tagen" value={c.due_in_days ?? ""} onChange={(e) => onChange({ due_in_days: e.target.value })} />
        <Input aria-label="Titel" className="sm:col-span-2" placeholder="Titel der Aufgabe" value={c.title ?? ""} onChange={(e) => onChange({ title: e.target.value })} />
      </div>;
    case "http":
      return <div className="grid gap-2">
        <div className="flex gap-2"><Select aria-label="Methode" value={c.method} onChange={(e) => onChange({ method: e.target.value })} className="!w-28">{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}</Select><Input aria-label="URL" className="font-mono text-xs" value={c.url ?? ""} onChange={(e) => onChange({ url: e.target.value })} /></div>
        <HeaderEditor value={(step.config.headers as Record<string, string>) ?? {}} onChange={(h) => onChange({ headers: h })} />
        <Textarea aria-label="Body (JSON oder Text)" rows={3} className="font-mono text-xs" placeholder='{"text": "Rechnung {{data.number}}"}' value={typeof step.config.body === "string" ? step.config.body : JSON.stringify(step.config.body ?? "", null, 2)}
          onChange={(e) => { let v: unknown = e.target.value; try { v = JSON.parse(e.target.value); } catch { /* Text */ } onChange({ body: v }); }} />
        <p className="text-xs text-slate-500">Antwort steht danach unter <code>{`{{steps.${step.id}.body…}}`}</code> bereit. Interne Netzadressen sind gesperrt.</p>
      </div>;
    case "python":
      return <div className="grid gap-2">
        <Textarea aria-label="Python-Code" rows={8} spellCheck={false} className="font-mono text-xs" value={c.code ?? ""} onChange={(e) => onChange({ code: e.target.value })} />
        <p className="text-xs text-slate-500">Eingabe <code>data</code> (mit <code>data[&quot;data&quot;]</code> und <code>data[&quot;steps&quot;]</code>), Ergebnis in <code>result</code>. Erlaubt sind <code>requests</code> (nur öffentliche Adressen), <code>re</code>, json, math, datetime, decimal u. a. – keine Dateien, keine Prozesse, max. 5 s CPU / 20 s Laufzeit / 256 MB.</p>
      </div>;
  }
}
