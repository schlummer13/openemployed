"use client";

import { RelDate } from "@/components/RelDate";
import { useFeedback } from "@/components/Feedback";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RECEIVABLE_STATUS, type Receivable } from "@/components/ReceivablePanel";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Table } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

type Item = Pick<Receivable, "status" | "principal_open" | "interest_open" | "costs_open" | "total_open" | "days_overdue" | "level"> & {
  invoice_id: string; number: string; customer_name: string; customer_type: "b2b" | "b2c"; issue_date: string; due_date: string; blocked: boolean;
};
interface Settings { enabled: boolean; auto_send: boolean; b2c_fee: string; b2b_flat_fee: boolean; deadline_days: number }
interface SettingsResponse { settings: Settings; base_rates: { valid_from: string; rate: string }[]; rate_missing: string | null; mail_configured: boolean }
interface RunResult { skipped: boolean; reason?: string; created: { invoice: string; number: string; level: number }[]; sent: string[]; errors: { invoice: string; error: string }[] }

const LEVEL = ["–", "Erinnerung", "Mahnung", "Letzte Mahnung"];

export default function ReceivablesPage() {
  const fb = useFeedback();
  const [data, setData] = useState<{ items: Item[]; total_open: string; overdue: string } | null>(null);
  const [cfg, setCfg] = useState<SettingsResponse | null>(null);
  const [rate, setRate] = useState({ valid_from: "", rate: "" });
  const [error, setError] = useState<string | null>(null);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void api<{ items: Item[]; total_open: string; overdue: string }>("GET", "/api/receivables").then(setData);
    void api<SettingsResponse>("GET", "/api/dunning-settings").then((c) => { setCfg(c); if (c.rate_missing) setRate((r) => ({ ...r, valid_from: c.rate_missing! })); });
  }, []);
  useEffect(load, [load]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }
  const runNow = () => act(async () => {
    const r = await api<RunResult>("POST", "/api/dunning/run");
    if (r.skipped) { setNotice(r.reason ?? "Mahnlauf läuft bereits"); return; }
    setNotice(`Mahnlauf: ${r.created.length} Schreiben erstellt, ${r.sent.length} versendet${r.errors.length ? `, ${r.errors.length} Fehler: ${r.errors.map((e) => `${e.invoice}: ${e.error}`).join("; ")}` : ""}.`);
  });
  const saveSettings = () => cfg && act(async () => { await api("PUT", "/api/dunning-settings", { ...cfg.settings, b2c_fee: toApiDecimal(String(cfg.settings.b2c_fee)) }); setNotice("Einstellungen gespeichert."); });
  const saveRate = () => act(async () => { await api("POST", "/api/base-rates", { valid_from: rate.valid_from, rate: toApiDecimal(rate.rate) }); setRate({ valid_from: "", rate: "" }); setNotice("Basiszinssatz gespeichert."); });
  const set = (patch: Partial<Settings>) => cfg && setCfg({ ...cfg, settings: { ...cfg.settings, ...patch } });

  return (
    <>
      <PageHeader title="Forderungen" description="Offene Posten, Zahlungseingänge und Mahnwesen. Der Mahnlauf läuft täglich um 07:00 Uhr; Zahlungen erfasst ihr an der jeweiligen Rechnung."
        actions={<Button variant="primary" onClick={runNow} disabled={busy}>Mahnlauf jetzt ausführen</Button>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {cfg?.rate_missing && <div className="mb-4"><Alert kind="warning">Der Basiszinssatz ab {formatDate(cfg.rate_missing)} ist noch nicht hinterlegt. Bitte den Wert der Deutschen Bundesbank unten eintragen – ohne ihn werden keine Mahnungen mit Verzugszinsen erstellt.</Alert></div>}

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Card><p className="text-xs text-slate-500">Offen gesamt</p><p className="text-2xl font-semibold tabular">{formatEuro(data?.total_open ?? "0")}</p></Card>
        <Card><p className="text-xs text-slate-500">davon überfällig (Hauptforderung)</p><p className="text-2xl font-semibold tabular text-red-700 dark:text-red-300">{formatEuro(data?.overdue ?? "0")}</p></Card>
        <Card><p className="text-xs text-slate-500">Offene Rechnungen</p><p className="text-2xl font-semibold tabular">{data?.items.length ?? 0}</p></Card>
      </div>

      <Card>
        {data && data.items.length === 0 ? <Empty>Keine offenen Posten – alles bezahlt.</Empty> : (
          <Table head={<tr>
            <th className="pb-2 font-medium">Rechnung</th><th className="pb-2 font-medium">Kunde</th><th className="pb-2 font-medium">Fällig</th>
            <th className="pb-2 pr-4 text-right font-medium">Offen</th><th className="pb-2 font-medium">Status</th><th className="pb-2 font-medium">Mahnstufe</th>
          </tr>}>
            {(data?.items ?? []).map((i) => {
              const [label, tone] = RECEIVABLE_STATUS[i.status];
              return (
                <tr key={i.invoice_id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className="py-2 tabular"><Link className="font-medium text-brand-700 hover:underline dark:text-brand-100" href={`/rechnungen/${i.invoice_id}`}>{i.number}</Link></td>
                  <td className="py-2">{i.customer_name} <span className="text-xs text-slate-500">{i.customer_type === "b2b" ? "Firma" : "Privat"}</span></td>
                  <td className="py-2 tabular">{formatDate(i.due_date)}<span className="block text-xs"><RelDate date={i.due_date} mode="due" /></span></td>
                  <td className="py-2 pr-4 text-right tabular">{formatEuro(i.total_open)}</td>
                  <td className="py-2"><Badge tone={tone}>{label}{i.status === "overdue" ? ` · ${i.days_overdue} T.` : ""}</Badge>{i.blocked && <span className="ml-1"><Badge tone="amber">gesperrt</Badge></span>}</td>
                  <td className="py-2">{LEVEL[i.level]}</td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      {cfg && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <h2 className="mb-3 font-medium">Mahnwesen</h2>
            <div className="space-y-2 text-sm">
              <label className="flex items-center gap-2"><input type="checkbox" checked={cfg.settings.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Täglicher Mahnlauf aktiv</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={cfg.settings.auto_send} onChange={(e) => set({ auto_send: e.target.checked })} /> Schreiben automatisch per E-Mail versenden {!cfg.mail_configured && <span className="text-xs text-amber-700">(E-Mail noch nicht eingerichtet → Schreiben bleiben zum manuellen Versand liegen)</span>}</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={cfg.settings.b2b_flat_fee} onChange={(e) => set({ b2b_flat_fee: e.target.checked })} /> 40 € Verzugspauschale bei Firmenkunden (§ 288 Abs. 5 BGB)</label>
              <div className="grid grid-cols-2 gap-3 pt-2">
                <Field label="Mahnkosten Privatkunden (€)" hint="nur tatsächliche Kosten, max. 10 €"><Input inputMode="decimal" value={String(cfg.settings.b2c_fee).replace(".", ",")} onChange={(e) => set({ b2c_fee: e.target.value })} /></Field>
                <Field label="Zahlungsfrist je Schreiben (Tage)"><Input type="number" min={5} max={30} value={cfg.settings.deadline_days} onChange={(e) => set({ deadline_days: Number(e.target.value) })} /></Field>
              </div>
              <p className="text-xs text-slate-500">Stufen: Zahlungserinnerung 7 Tage, Mahnung 21 Tage, letzte Mahnung 35 Tage nach Fälligkeit (mind. 7 Tage Abstand). Zinsen: Firmen 9, Privat 5 Prozentpunkte über Basiszins.</p>
              <Button variant="primary" onClick={saveSettings} disabled={busy}>Speichern</Button>
            </div>
          </Card>
          <Card>
            <h2 className="mb-3 font-medium">Basiszinssatz (§ 247 BGB)</h2>
            <ul className="mb-3 max-h-40 overflow-y-auto text-sm tabular">
              {cfg.base_rates.map((b) => <li key={b.valid_from} className="flex justify-between py-0.5"><span>ab {formatDate(b.valid_from)}</span><span>{b.rate.replace(".", ",")} %</span></li>)}
            </ul>
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Gültig ab (1.1. oder 1.7.)"><Input type="date" value={rate.valid_from} onChange={(e) => setRate({ ...rate, valid_from: e.target.value })} /></Field>
              <Field label="Satz (%)"><Input inputMode="decimal" className="w-24" value={rate.rate} onChange={(e) => setRate({ ...rate, rate: e.target.value })} /></Field>
              <Button onClick={saveRate} disabled={busy || !rate.valid_from || !rate.rate}>Eintragen</Button>
            </div>
            <p className="mt-2 text-xs text-slate-500">Quelle: Deutsche Bundesbank, Veröffentlichung jeweils Anfang Januar und Juli.</p>
          </Card>
        </div>
      )}
    </>
  );
}
