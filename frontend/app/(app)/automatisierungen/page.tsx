"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { Alert, Badge, Button, Card, PageHeader, Tabs } from "@/components/ui";
import { WorkflowBuilder } from "@/components/WorkflowBuilder";
import { api, HttpError } from "@/lib/http";

interface Run { started_at: string; finished_at?: string | null; status: "running" | "ok" | "skipped" | "error"; summary: string | null; trigger: string }
interface Automation { key: string; label: string; description: string; schedule: string; enabled: boolean; unavailable: string | null; last: Run | null; recent: Run[] }

const STATUS: Record<Run["status"], [string, "green" | "slate" | "red" | "blue"]> = {
  ok: ["erfolgreich", "green"], skipped: ["übersprungen", "slate"], error: ["Fehler", "red"], running: ["läuft", "blue"],
};

function ago(iso: string): string {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "gerade eben";
  if (m < 60) return `vor ${m} Min.`;
  if (m < 60 * 24) return `vor ${Math.round(m / 60)} Std.`;
  return new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

interface AiStatus { active: boolean; ready: boolean; hint: string | null; label: string | null; ollama_model: string }

export default function AutomationsPage() {
  const [items, setItems] = useState<Automation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [tab, setTab] = useState<"system" | "workflows">("system");
  useEffect(() => { if (new URLSearchParams(window.location.search).get("tab") === "workflows") setTab("workflows"); }, []);
  const load = useCallback(() => { void api<Automation[]>("GET", "/api/automations").then(setItems); }, []);
  useEffect(load, [load]);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key); setError(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(null); }
  }

  return (
    <>
      <PageHeader title="Automatisierungen" description="Was OpenEmployed im Hintergrund für euch erledigt. Gebucht oder festgeschrieben wird nie automatisch – Automatisierungen erzeugen Entwürfe, eindeutige Zuordnungen und Aufgaben." />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[{ value: "system", label: "Eingebaut" }, { value: "workflows", label: "Eigene Workflows" }]} /></div>
      {tab === "workflows" ? <WorkflowBuilder /> : <>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <SettingsCard />
      <div className="grid gap-4 lg:grid-cols-2">
        {(items ?? []).map((a) => {
          const last = a.last;
          return (
            <Card key={a.key} className="flex flex-col">
              <div className="flex items-start gap-3">
                <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${a.enabled && !a.unavailable ? "bg-brand-50 text-brand-600 dark:bg-brand-950 dark:text-brand-300" : "bg-slate-100 text-slate-400 dark:bg-slate-800"}`}><Icon name="zap" /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <h2 className="font-semibold">{a.label}</h2>
                    <button role="switch" aria-checked={a.enabled} aria-label={`${a.label} ${a.enabled ? "ausschalten" : "einschalten"}`} disabled={busy === a.key}
                      onClick={() => act(a.key, () => api("PUT", `/api/automations/${a.key}`, { enabled: !a.enabled }))}
                      className={`relative h-6 w-11 shrink-0 rounded-full transition ${a.enabled ? "bg-brand-600" : "bg-slate-300 dark:bg-slate-700"}`}>
                      <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition ${a.enabled ? "left-[22px]" : "left-0.5"}`} />
                    </button>
                  </div>
                  <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{a.description}</p>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span className="rounded-md bg-slate-100 px-2 py-0.5 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{a.schedule}</span>
                {a.unavailable ? <Badge tone="amber">{a.unavailable}</Badge> : !a.enabled ? <Badge>ausgeschaltet</Badge> : null}
                {last && <><Badge tone={STATUS[last.status][1]}>{STATUS[last.status][0]}</Badge><span>{ago(last.started_at)}{last.trigger === "manual" ? " (manuell)" : ""}</span></>}
                {!last && <span>noch nicht gelaufen</span>}
              </div>
              {last?.summary && <p className="mt-2 text-sm">{last.summary}</p>}
              <div className="mt-auto flex items-center gap-2 pt-4">
                <Button onClick={() => act(a.key, () => api("POST", `/api/automations/${a.key}/run`))} disabled={busy !== null}>{busy === a.key ? "Läuft …" : "Jetzt ausführen"}</Button>
                {a.recent.length > 1 && <Button variant="ghost" onClick={() => setOpen(open === a.key ? null : a.key)}>{open === a.key ? "Verlauf ausblenden" : "Verlauf"}</Button>}
              </div>
              {open === a.key && (
                <ul className="mt-3 space-y-1 border-t border-slate-100 pt-3 text-xs dark:border-slate-800">
                  {a.recent.map((r) => <li key={r.started_at} className="flex gap-2"><Badge tone={STATUS[r.status][1]}>{STATUS[r.status][0]}</Badge><span className="text-slate-500">{ago(r.started_at)}</span><span className="truncate">{r.summary}</span></li>)}
                </ul>
              )}
            </Card>
          );
        })}
      </div>
      </>}
    </>
  );
}


interface FinanceSettings { min_liquidity: string; monthly_fixed_costs: string; offer_followup_days: number; offer_auto_reminder: boolean; welcome_mail: boolean }

function SettingsCard() {
  const [s, setS] = useState<FinanceSettings | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    void api<FinanceSettings>("GET", "/api/finance/settings").then((x) => setS({ ...x, min_liquidity: x.min_liquidity.replace(".", ","), monthly_fixed_costs: x.monthly_fixed_costs.replace(".", ",") }));
    void api<AiStatus>("GET", "/api/ai/status").then(setAi);
  }, []);
  // Während das Modell lädt, Status alle 15 Sekunden aktualisieren
  useEffect(() => {
    if (!ai?.active || ai.ready) return;
    const t = setInterval(() => { void api<AiStatus>("GET", "/api/ai/status").then(setAi).catch(() => undefined); }, 15000);
    return () => clearInterval(t);
  }, [ai?.active, ai?.ready]);
  if (!s) return null;
  const save = async () => {
    setMsg(null);
    try {
      await api("PUT", "/api/finance/settings", { ...s, min_liquidity: s.min_liquidity.replace(/\./g, "").replace(",", "."), monthly_fixed_costs: s.monthly_fixed_costs.replace(/\./g, "").replace(",", ".") });
      setMsg("Gespeichert.");
    } catch (e) { setMsg(e instanceof HttpError ? e.message : "Speichern fehlgeschlagen"); }
  };
  return (
    <Card className="mb-6">
      <div className="grid gap-6 lg:grid-cols-[1fr_auto]">
        <div>
          <h2 className="mb-3 font-semibold">Einstellungen für Liquidität und Automatisierungen</h2>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-sm"><span className="mb-1.5 block text-[13px] font-medium">Mindestreserve (€)</span><input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900" inputMode="decimal" value={s.min_liquidity} onChange={(e) => setS({ ...s, min_liquidity: e.target.value })} /></label>
            <label className="text-sm"><span className="mb-1.5 block text-[13px] font-medium">Fixkosten pro Monat (€)</span><input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900" inputMode="decimal" value={s.monthly_fixed_costs} onChange={(e) => setS({ ...s, monthly_fixed_costs: e.target.value })} /></label>
            <label className="text-sm"><span className="mb-1.5 block text-[13px] font-medium">Angebote nachfassen nach (Tagen)</span><input type="number" min={2} max={60} className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900" value={s.offer_followup_days} onChange={(e) => setS({ ...s, offer_followup_days: Number(e.target.value) })} /></label>
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={s.welcome_mail} onChange={(e) => setS({ ...s, welcome_mail: e.target.checked })} /> Neue Kunden erhalten automatisch eine Willkommensmail mit Kundenportal-Zugang</label>
          <div className="mt-3 flex items-center gap-3"><Button variant="primary" onClick={save}>Speichern</Button>{msg && <span className="text-sm text-slate-500">{msg}</span>}</div>
        </div>
        <div className="rounded-xl bg-slate-50 p-4 text-sm lg:w-72 dark:bg-slate-950">
          <p className="font-semibold">KI für Belege</p>
          {!ai?.active ? <p className="mt-1 flex items-center gap-2"><Badge tone="slate">aus</Badge> KI ist abgeschaltet (AI_PROVIDER=none)</p>
            : ai.ready ? <p className="mt-1 flex flex-wrap items-center gap-2"><Badge tone="green">bereit</Badge> {ai.label}</p>
            : <p className="mt-1 flex flex-wrap items-center gap-2"><Badge tone="amber">lädt</Badge> {ai.hint}</p>}
          <p className="mt-2 text-xs text-slate-500">Eingebaute lokale KI (Ollama mit {ai?.ollama_model ?? "qwen2.5vl:7b"}) – läuft auf dem eigenen Server, keine Kosten je Beleg, keine Daten an Dritte. Das Modell wird beim ersten Start automatisch geladen. E-Rechnungen werden immer ohne KI gelesen.</p>
        </div>
      </div>
    </Card>
  );
}
