"use client";

import { useFeedback } from "@/components/Feedback";
import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Select, Stat, Table, Tabs } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, formatDate, HttpError, todayIso } from "@/lib/http";

interface Candidate { invoice_id: string; number: string; customer_name: string; total_open: string; exact: boolean; by_reference: boolean }
interface Tx { id: string; source: string; booked_on: string; amount: string; counterparty: string | null; reference: string | null; status: "open" | "matched" | "ignored"; candidates: Candidate[]; receipt_candidates?: { receipt_id: string; supplier_name: string; invoice_number: string | null; description: string; amount: string; by_reference: boolean; by_name: boolean }[] }
interface TaxKey { tax_case: string; account: string | null; bu_key: string | null; required: boolean }
interface DatevSettings {
  consultant_number: number | null; client_number: number | null; chart: string; account_length: number; fiscal_year_start: number;
  bank_account: string; receivables_account: string; payables_account: string; use_personal_accounts: boolean; confirmed_by_advisor: boolean; tax_keys: TaxKey[];
}
interface ExportRow { id: string; period_from: string; period_to: string; kind: string; bookings: number; sha256: string; created_by: string; created_at: string }

const KIND = { final: ["final", "green"], correction: ["Korrektur", "amber"], preview: ["Vorschau", "slate"] } as const;

function monthRange(offset: number): { from: string; to: string } {
  const d = new Date();
  const f = new Date(d.getFullYear(), d.getMonth() + offset, 1);
  const t = new Date(d.getFullYear(), d.getMonth() + offset + 1, 0);
  const iso = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  return { from: iso(f), to: iso(t) };
}

export default function AccountingPage() {
  const fb = useFeedback();
  const [tab, setTab] = useState<"journal" | "bank" | "datev">("journal");
  const [error, setError] = useState<string | null>(null);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); } catch (e) { setError(e instanceof HttpError ? e.message : e instanceof Error ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Buchhaltung" description="Doppelte Buchführung nach SKR03/SKR04: Rechnungen und Zahlungen werden nach Regeln gebucht, Eingangsbelege kontiert die KI auf Klick. Festschreibung nach GoBD. Dazu Bankabgleich und DATEV-Übergabe." />
      <Tabs value={tab} onChange={setTab} items={[{ value: "journal", label: "Journal & Konten" }, { value: "bank", label: "Bank & Zahlungsabgleich" }, { value: "datev", label: "DATEV-Export" }]} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {tab === "journal" ? <Journal /> : tab === "bank" ? <Bank act={act} busy={busy} setNotice={setNotice} /> : <Datev act={act} busy={busy} setNotice={setNotice} />}
    </>
  );
}

type Props = { act: (fn: () => Promise<void>) => Promise<void>; busy: boolean; setNotice: (s: string | null) => void };

function Bank({ act, busy, setNotice }: Props) {
  const [data, setData] = useState<{ transactions: Tx[]; open: number; fints_ready: boolean } | null>(null);
  const load = useCallback(() => { void api<{ transactions: Tx[]; open: number; fints_ready: boolean }>("GET", "/api/bank/transactions").then(setData); }, []);
  useEffect(load, [load]);

  const upload = (file: File) => act(async () => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/bank/import", { method: "POST", body: fd });
    const body = (await res.json().catch(() => ({}))) as { rows?: number; new?: number; duplicates?: number; auto_matched?: number; detail?: string };
    if (!res.ok) throw new Error(body.detail ?? `Fehler ${res.status}`);
    setNotice(`${body.rows} Umsätze gelesen, ${body.new} neu, ${body.duplicates} bereits vorhanden, ${body.auto_matched} automatisch zugeordnet.`);
    load();
  });
  return (
    <>
      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="cursor-pointer inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800">
            Kontoauszug importieren (CAMT.053, MT940, CSV)
            <input type="file" aria-label="Kontoauszug" accept=".csv,.xml,.sta,.mt940,.940,.txt,text/csv,application/xml" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
          </label>
          <span className="text-sm text-slate-500">{data ? `${data.open} offene Umsätze` : ""}</span>
        </div>
        <p className="mt-2 text-xs text-slate-500">Eingänge: automatisch bei Rechnungsnummer im Verwendungszweck und exaktem Betrag. Abbuchungen: automatisch, wenn genau ein gebuchter Beleg mit gleichem Betrag und passendem Lieferanten bzw. Rechnungsnummer offen ist – der Beleg gilt dann als bezahlt. Sonst Vorschlag zum Bestätigen.</p>
      </Card>
      <Connections act={act} busy={busy} setNotice={setNotice} onSynced={load} />
      <Card>
        {data && data.transactions.length === 0 ? <Empty>Noch keine Bankumsätze importiert.</Empty> : (
          <Table head={<tr><th className="pb-2 font-medium">Datum</th><th className="pb-2 font-medium">Gegenpartei / Zweck</th><th className="pb-2 pr-4 text-right font-medium">Betrag</th><th className="pb-2 font-medium">Zuordnung</th></tr>}>
            {(data?.transactions ?? []).map((t) => (
              <tr key={t.id} className="align-top">
                <td className="py-2 tabular">{formatDate(t.booked_on)}</td>
                <td className="py-2">{t.counterparty ?? "–"}<br /><span className="text-xs text-slate-500">{t.reference}</span></td>
                <td className={`py-2 pr-4 text-right tabular ${Number(t.amount) < 0 ? "text-red-700 dark:text-red-300" : ""}`}>{formatEuro(t.amount)}</td>
                <td className="py-2">
                  {t.status === "matched" && <Badge tone="green">zugeordnet</Badge>}
                  {t.status === "ignored" && <Badge tone="slate">ignoriert</Badge>}
                  {t.status === "open" && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      {t.candidates.map((c) => (
                        <Button key={c.invoice_id} className="!px-2 !h-7 !py-0 !text-xs" disabled={busy} onClick={() => act(async () => { await api("POST", `/api/bank/transactions/${t.id}/match`, { invoice_id: c.invoice_id }); setNotice(`Zahlung ${c.number} zugeordnet.`); load(); })}>
                          {c.number} · {c.customer_name} · offen {formatEuro(c.total_open)}{c.exact ? " ✓" : ""}
                        </Button>
                      ))}
                      {t.candidates.length === 0 && Number(t.amount) > 0 && <span className="text-xs text-slate-500">keine passende Rechnung</span>}
                      {(t.receipt_candidates ?? []).map((c) => (
                        <Button key={c.receipt_id} className="!px-2 !h-7 !py-0 !text-xs" disabled={busy} title={c.description}
                          onClick={() => act(async () => { await api("POST", `/api/bank/transactions/${t.id}/match-receipt`, { receipt_id: c.receipt_id }); setNotice(`Zahlung an ${c.supplier_name} zugeordnet – Beleg ist bezahlt.`); load(); })}>
                          {c.supplier_name}{c.invoice_number ? ` · ${c.invoice_number}` : ""}{c.by_reference || c.by_name ? " ✓" : ""}
                        </Button>
                      ))}
                      {Number(t.amount) < 0 && !(t.receipt_candidates ?? []).length && <span className="text-xs text-slate-500">kein offener Beleg mit diesem Betrag</span>}
                      <Button className="!px-2 !h-7 !py-0 !text-xs" disabled={busy} onClick={() => act(async () => { await api("POST", `/api/bank/transactions/${t.id}/ignore`, { note: Number(t.amount) < 0 ? "Ausgabe – Beleg unter Belege erfassen" : null }); load(); })}>ignorieren</Button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}

function Datev({ act, busy, setNotice }: Props) {
  const fb = useFeedback();
  const [s, setS] = useState<DatevSettings | null>(null);
  const [exports, setExports] = useState<ExportRow[]>([]);
  const [range, setRange] = useState(monthRange(-1));
  const [check, setCheck] = useState<{ bookings: number; problems: string[] } | null>(null);
  const load = useCallback(() => {
    void api<DatevSettings>("GET", "/api/datev/settings").then(setS);
    void api<ExportRow[]>("GET", "/api/datev/exports").then(setExports);
  }, []);
  useEffect(load, [load]);
  useEffect(() => { setCheck(null); }, [range]);

  async function download(final: boolean) {
    await act(async () => {
      if (final && !await fb.confirm(`Zeitraum ${formatDate(range.from)} – ${formatDate(range.to)} final exportieren?\n\nDie enthaltenen Buchungen werden als übergeben markiert; spätere Änderungen erscheinen als Korrekturstapel.`)) return;
      const res = await fetch("/api/datev/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ period_from: range.from, period_to: range.to, final }) });
      if (!res.ok) { const b = (await res.json().catch(() => ({}))) as { detail?: string }; throw new Error(b.detail ?? `Fehler ${res.status}`); }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = (res.headers.get("content-disposition") ?? "").match(/filename="(.+)"/)?.[1] ?? "DATEV.zip"; a.click();
      URL.revokeObjectURL(url);
      setNotice(`${res.headers.get("x-export-kind") === "preview" ? "Vorschau" : "Export"} mit ${res.headers.get("x-bookings")} Buchungen erstellt.`);
      load();
    });
  }
  if (!s) return null;
  const set = (patch: Partial<DatevSettings>) => setS({ ...s, ...patch });

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <h2 className="mb-3 font-medium">Monatsübergabe</h2>
        <div className="mb-3 flex flex-wrap gap-2">
          <Button onClick={() => setRange(monthRange(-1))}>Vormonat</Button>
          <Button onClick={() => setRange(monthRange(0))}>Laufender Monat</Button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Von"><Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
          <Field label="Bis"><Input type="date" max={todayIso()} value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => act(async () => setCheck(await api("POST", "/api/datev/check", { period_from: range.from, period_to: range.to })))} disabled={busy}>Prüfen</Button>
          <Button onClick={() => download(false)} disabled={busy}>Vorschau herunterladen</Button>
          <Button variant="primary" onClick={() => download(true)} disabled={busy || (check !== null && check.problems.length > 0)}>Final exportieren</Button>
        </div>
        {check && (
          <div className="mt-3 text-sm">
            <p>{check.bookings} neue Buchungen im Zeitraum.</p>
            {check.problems.length > 0 && <ul className="mt-1 list-disc pl-5 text-red-700 dark:text-red-300">{check.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          </div>
        )}
        <h3 className="mb-2 mt-6 text-sm font-medium text-slate-500">Bisherige Exporte</h3>
        {exports.length === 0 ? <p className="text-sm text-slate-500">Noch keine finalen Exporte.</p> : (
          <ul className="space-y-1 text-sm">
            {exports.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>{formatDate(e.period_from)} – {formatDate(e.period_to)} · {e.bookings} Buchungen · {new Date(e.created_at).toLocaleDateString("de-DE")} <Badge tone={KIND[e.kind as keyof typeof KIND]?.[1] ?? "slate"}>{KIND[e.kind as keyof typeof KIND]?.[0] ?? e.kind}</Badge></span>
                <a className="text-brand-700 hover:underline dark:text-brand-100" href={`/api/datev/exports/${e.id}`}>ZIP</a>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card>
        <h2 className="mb-3 font-medium">DATEV-Einstellungen</h2>
        {!s.confirmed_by_advisor && <div className="mb-3"><Alert kind="warning">Kontenzuordnung und BU-Schlüssel sind Vorschläge (SKR04). Bitte vom Steuerberater bestätigen lassen und einen Testimport machen.</Alert></div>}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Kontenrahmen" hint="einmal festlegen – nach der ersten Buchung nicht mehr änderbar"><Select value={s.chart} onChange={(e) => set({ chart: e.target.value })}><option value="SKR04">SKR04 (Abschlussgliederung)</option><option value="SKR03">SKR03 (Prozessgliederung)</option></Select></Field>
          <Field label="Beraternummer"><Input inputMode="numeric" value={s.consultant_number ?? ""} onChange={(e) => set({ consultant_number: e.target.value ? Number(e.target.value) : null })} /></Field>
          <Field label="Mandantennummer"><Input inputMode="numeric" value={s.client_number ?? ""} onChange={(e) => set({ client_number: e.target.value ? Number(e.target.value) : null })} /></Field>
          <Field label="Bankkonto (Sachkonto)"><Input value={s.bank_account} onChange={(e) => set({ bank_account: e.target.value })} /></Field>
          <Field label="Wirtschaftsjahr beginnt (Monat)"><Input type="number" min={1} max={12} value={s.fiscal_year_start} onChange={(e) => set({ fiscal_year_start: Number(e.target.value) })} /></Field>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={s.use_personal_accounts} onChange={(e) => set({ use_personal_accounts: e.target.checked })} /> Mit Personenkonten (Debitoren/Kreditoren)</label>
        <div className="overflow-x-auto"><table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs text-slate-500"><tr><th className="pb-1 font-medium">Steuerfall</th><th className="pb-1 font-medium">Konto</th><th className="pb-1 font-medium">BU-Schlüssel</th></tr></thead>
          <tbody>
            {s.tax_keys.map((k, i) => (
              <tr key={k.tax_case}>
                <td className="py-1 pr-2 font-mono text-xs">{k.tax_case}{k.required && !k.bu_key && <span className="ml-1 text-red-600">*</span>}</td>
                <td className="py-1 pr-2"><Input aria-label={`Konto ${k.tax_case}`} className="w-24" placeholder={k.tax_case.startsWith("IN_") ? "Kategorie" : ""} value={k.account ?? ""} onChange={(e) => set({ tax_keys: s.tax_keys.map((x, j) => j === i ? { ...x, account: e.target.value || null } : x) })} /></td>
                <td className="py-1"><Input aria-label={`BU-Schlüssel ${k.tax_case}`} className="w-20" value={k.bu_key ?? ""} onChange={(e) => set({ tax_keys: s.tax_keys.map((x, j) => j === i ? { ...x, bu_key: e.target.value || null } : x) })} /></td>
              </tr>
            ))}
          </tbody>
        </table></div>
        <p className="mt-1 text-xs text-slate-500">* Pflicht für Reverse-Charge-Fälle – Schlüssel laut aktueller DATEV-Tabelle vom Steuerberater.</p>
        <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={s.confirmed_by_advisor} onChange={(e) => set({ confirmed_by_advisor: e.target.checked })} /> Vom Steuerberater bestätigt</label>
        <div className="mt-3"><Button variant="primary" disabled={busy} onClick={() => act(async () => { setS(await api<DatevSettings>("PUT", "/api/datev/settings", s)); setNotice("DATEV-Einstellungen gespeichert."); })}>Speichern</Button></div>
      </Card>
    </div>
  );
}


interface Connection { id: string; label: string; bank_code: string; server_url: string; login_name: string; iban: string; last_sync_at: string | null }
interface FintsResult { status: "ok" | "tan"; token?: string; challenge?: string; decoupled?: boolean; challenge_html?: string | null; new?: number; rows?: number; auto_matched?: number }

function Connections({ act, busy, setNotice, onSynced }: Props & { onSynced: () => void }) {
  const fb = useFeedback();
  const [data, setData] = useState<{ connections: Connection[]; fints_ready: boolean } | null>(null);
  const [form, setForm] = useState<{ label: string; bank_code: string; server_url: string; login_name: string; iban: string } | null>(null);
  const [sync, setSync] = useState<{ id: string; label: string; pin: string; days: number } | null>(null);
  const [tan, setTan] = useState<{ token: string; challenge: string; decoupled: boolean; value: string } | null>(null);
  const load = useCallback(() => { void api<{ connections: Connection[]; fints_ready: boolean }>("GET", "/api/bank/connections").then(setData); }, []);
  useEffect(load, [load]);

  function finish(r: FintsResult) {
    if (r.status === "tan" && r.token) { setTan({ token: r.token, challenge: r.challenge ?? "Bitte freigeben", decoupled: !!r.decoupled, value: "" }); return; }
    setTan(null); setSync(null);
    setNotice(`Onlinebanking: ${r.rows} Umsätze abgerufen, ${r.new} neu, ${r.auto_matched} automatisch zugeordnet.`);
    load(); onSynced();
  }

  return (
    <Card className="mb-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold">Onlinebanking (FinTS/HBCI)</h2>
          <p className="text-xs text-slate-500">Für jede deutsche Bank mit Onlinebanking. Die PIN wird bei jedem Abruf eingegeben und nie gespeichert.</p>
        </div>
        {!form && <Button onClick={() => setForm({ label: "", bank_code: "", server_url: "", login_name: "", iban: "" })}>+ Bankverbindung</Button>}
      </div>
      {data && !data.fints_ready && <div className="mb-3"><Alert kind="warning">Für den Abruf ist eine bei der Deutschen Kreditwirtschaft registrierte FinTS-Produkt-ID nötig (kostenlos): <code>FINTS_PRODUCT_ID</code> in der .env setzen. Bis dahin Kontoauszüge als CAMT.053 oder MT940 aus dem Onlinebanking exportieren und oben importieren.</Alert></div>}
      {(data?.connections ?? []).length > 0 && (
        <ul className="mb-2 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {data!.connections.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span><strong>{c.label}</strong> <span className="text-xs text-slate-500">{c.iban.replace(/(.{4})/g, "$1 ").trim()} · BLZ {c.bank_code}{c.last_sync_at ? ` · zuletzt ${new Date(c.last_sync_at).toLocaleString("de-DE")}` : ""}</span></span>
              <span className="flex gap-2">
                <Button className="!px-2.5 !h-7 !py-0 !text-xs" variant="primary" disabled={busy || !data?.fints_ready} onClick={() => { setTan(null); setSync({ id: c.id, label: c.label, pin: "", days: 30 }); }}>Umsätze abrufen</Button>
                <Button className="!px-2.5 !h-7 !py-0 !text-xs" variant="ghost" onClick={() => act(async () => { if (!await fb.confirm(`Bankverbindung „${c.label}“ entfernen? Bereits importierte Umsätze bleiben erhalten.`)) return; await api("DELETE", `/api/bank/connections/${c.id}`); load(); })}>entfernen</Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {form && (
        <form className="mt-2 grid gap-3 md:grid-cols-3" onSubmit={(e) => { e.preventDefault(); void act(async () => { await api("POST", "/api/bank/connections", form); setForm(null); load(); }); }}>
          <Field label="Bezeichnung"><Input required placeholder="z. B. Sparkasse Geschäftskonto" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></Field>
          <Field label="IBAN"><Input required value={form.iban} onChange={(e) => setForm({ ...form, iban: e.target.value.toUpperCase() })} /></Field>
          <Field label="Bankleitzahl"><Input required inputMode="numeric" maxLength={8} value={form.bank_code} onChange={(e) => setForm({ ...form, bank_code: e.target.value })} /></Field>
          <Field label="FinTS-Server-URL" hint="steht auf der Website der Bank (z. B. „HBCI/FinTS-Zugang“)"><Input required placeholder="https://…" value={form.server_url} onChange={(e) => setForm({ ...form, server_url: e.target.value })} /></Field>
          <Field label="Anmeldename / Legitimations-ID"><Input required autoComplete="off" value={form.login_name} onChange={(e) => setForm({ ...form, login_name: e.target.value })} /></Field>
          <div className="flex items-end gap-2"><Button type="submit" variant="primary" disabled={busy}>Speichern</Button><Button onClick={() => setForm(null)}>Abbrechen</Button></div>
        </form>
      )}
      {sync && !tan && (
        <form className="mt-3 flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 p-3 dark:border-slate-700" onSubmit={(e) => { e.preventDefault(); const s = sync; void act(async () => { const r = await api<FintsResult>("POST", `/api/bank/connections/${s.id}/fints`, { pin: s.pin, days: s.days }); setSync({ ...s, pin: "" }); finish(r); }); }}>
          <Field label={`PIN für ${sync.label}`}><Input type="password" required autoComplete="off" value={sync.pin} onChange={(e) => setSync({ ...sync, pin: e.target.value })} /></Field>
          <Field label="Zeitraum (Tage)"><Input type="number" min={1} max={90} value={sync.days} onChange={(e) => setSync({ ...sync, days: Number(e.target.value) })} /></Field>
          <Button type="submit" variant="primary" disabled={busy || !sync.pin}>Abrufen</Button>
          <Button onClick={() => setSync(null)}>Abbrechen</Button>
        </form>
      )}
      {tan && (
        <form className="mt-3 space-y-2 rounded-xl border border-brand-200 bg-brand-50/50 p-3 dark:border-brand-900 dark:bg-brand-950/30" onSubmit={(e) => { e.preventDefault(); const t = tan; void act(async () => finish(await api<FintsResult>("POST", "/api/bank/fints/tan", { token: t.token, tan: t.value }))); }}>
          <p className="text-sm font-medium">{tan.challenge}</p>
          {tan.decoupled ? <p className="text-xs text-slate-500">Bitte in der Banking-App freigeben und dann bestätigen.</p>
            : <Field label="TAN"><Input required autoComplete="one-time-code" value={tan.value} onChange={(e) => setTan({ ...tan, value: e.target.value })} /></Field>}
          <div className="flex gap-2"><Button type="submit" variant="primary" disabled={busy}>{tan.decoupled ? "In der App freigegeben" : "TAN senden"}</Button><Button onClick={() => { setTan(null); setSync(null); }}>Abbrechen</Button></div>
        </form>
      )}
    </Card>
  );
}


interface JLine { account: string; name: string | null; debit: string; credit: string; note: string | null }
interface Entry { id: string; number: string; entry_date: string; description: string; document_ref: string | null; source_type: string; method: "rule" | "claude" | "ai" | "manual"; ai_model: string | null; ai_reasoning: string | null; lines: JLine[] }
interface Balances { chart: string; accounts: { account: string; name: string | null; type: string | null; debit: string; credit: string; balance: string }[]; revenue: string; expenses: string; result: string; total_debit: string; total_credit: string; balanced: boolean }

const SOURCE: Record<string, string> = { invoice: "Ausgangsrechnung", payment: "Zahlungseingang", payment_reversal: "Rückbuchung", receipt: "Eingangsbeleg", receipt_payment: "Zahlungsausgang", manual: "manuell" };

function Journal() {
  const year = new Date().getFullYear();
  const [range, setRange] = useState({ from: `${year}-01-01`, to: todayIso() });
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [bal, setBal] = useState<Balances | null>(null);
  const [view, setView] = useState<"susa" | "journal">("susa");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    const q = `?start=${range.from}&end=${range.to}`;
    api<{ entries: Entry[] }>("GET", `/api/accounting/journal${q}`).then((j) => setEntries(j.entries)).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
    void api<Balances>("GET", `/api/accounting/balances${q}`).then(setBal);
  }, [range]);
  useEffect(load, [load]);
  const claude = (entries ?? []).filter((e) => e.method === "claude" || e.method === "ai").length;

  return (
    <>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <GobdCard from={range.from} to={range.to} />
      <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Erlöse" icon="trend" tone="green" value={bal ? formatEuro(bal.revenue) : "…"} hint={bal ? `Kontenrahmen ${bal.chart}` : undefined} />
        <Stat label="Aufwand" icon="receipt" tone="amber" value={bal ? formatEuro(bal.expenses) : "…"} />
        <Stat label="Ergebnis (vor Steuern)" icon="percent" tone={bal && Number(bal.result) < 0 ? "red" : "brand"} value={bal ? formatEuro(bal.result) : "…"} hint="laut Buchungsjournal" />
        <Stat label="Buchungssätze" icon="file" value={entries ? String(entries.length) : "…"} hint={entries ? `${claude} von der KI kontiert · ${bal?.balanced ? "Soll = Haben ✓" : "prüfen!"}` : undefined} />
      </div>
      <Card>
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div className="flex gap-2">
            {(["susa", "journal"] as const).map((v) => (
              <Button key={v} variant={view === v ? "primary" : "secondary"} onClick={() => setView(v)}>{v === "susa" ? "Summen- und Saldenliste" : "Buchungsjournal"}</Button>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <Field label="Von"><Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
            <Field label="Bis"><Input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
          </div>
        </div>
        {view === "susa" ? (
          !bal || bal.accounts.length === 0 ? <Empty>Noch keine Buchungen im Zeitraum.</Empty> : (
            <Table head={<tr><th>Konto</th><th>Bezeichnung</th><th className="text-right">Soll</th><th className="text-right">Haben</th><th className="pr-1 text-right">Saldo</th></tr>}>
              {bal.accounts.map((a) => (
                <tr key={a.account} className="tabular">
                  <td className="font-mono text-xs">{a.account}</td>
                  <td>{a.name ?? "–"}</td>
                  <td className="text-right">{formatEuro(a.debit)}</td>
                  <td className="text-right">{formatEuro(a.credit)}</td>
                  <td className="pr-1 text-right font-medium">{formatEuro(Math.abs(Number(a.balance)).toFixed(2))} {Number(a.balance) >= 0 ? "S" : "H"}</td>
                </tr>
              ))}
              <tr className="font-semibold tabular"><td colSpan={2}>Summe</td><td className="text-right">{formatEuro(bal.total_debit)}</td><td className="text-right">{formatEuro(bal.total_credit)}</td><td className="pr-1 text-right">{bal.balanced ? "ausgeglichen" : "Differenz!"}</td></tr>
            </Table>
          )
        ) : (
          !entries || entries.length === 0 ? <Empty>Noch keine Buchungen im Zeitraum.</Empty> : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {entries.map((e) => (
                <li key={e.id} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span><span className="font-mono text-xs text-slate-500">{e.number}</span> · {formatDate(e.entry_date)} · <strong>{e.description}</strong>{e.document_ref ? <span className="text-slate-500"> · Beleg {e.document_ref}</span> : null}</span>
                    <span className="flex gap-1.5"><Badge>{SOURCE[e.source_type] ?? e.source_type}</Badge>{e.method === "claude" || e.method === "ai" ? <Badge tone="blue">KI</Badge> : <Badge>Regel</Badge>}</span>
                  </div>
                  <div className="overflow-x-auto"><table className="mt-2 w-full max-w-2xl text-xs tabular">
                    <tbody>
                      {e.lines.map((l, i) => (
                        <tr key={i}>
                          <td className="w-16 py-0.5 font-mono">{l.account}</td>
                          <td className="py-0.5 text-slate-600 dark:text-slate-300">{l.name}{l.note ? <span className="text-slate-400"> – {l.note}</span> : null}</td>
                          <td className="w-28 py-0.5 text-right">{Number(l.debit) > 0 ? `${formatEuro(l.debit)} S` : ""}</td>
                          <td className="w-28 py-0.5 text-right">{Number(l.credit) > 0 ? `${formatEuro(l.credit)} H` : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table></div>
                  {e.ai_reasoning && <p className="mt-1.5 max-w-2xl rounded-lg bg-brand-50 px-2.5 py-1.5 text-xs text-brand-900 dark:bg-brand-950 dark:text-brand-100">Begründung ({e.ai_model}): {e.ai_reasoning}</p>}
                </li>
              ))}
            </ul>
          )
        )}
      </Card>
    </>
  );
}


function GobdCard({ from, to }: { from: string; to: string }) {
  const [status, setStatus] = useState<{ chain_ok: boolean; records_checked: number; locked_periods: string[]; last_locked: string | null } | null>(null);
  useEffect(() => { void api<typeof status>("GET", "/api/gobd/status").then(setStatus).catch(() => setStatus(null)); }, []);
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className={`grid size-9 place-items-center rounded-xl ${status?.chain_ok === false ? "bg-red-50 text-red-600 dark:bg-red-950" : "bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400"}`}><Icon name="shield" /></span>
          <div>
            <p className="font-semibold">Festschreibung (GoBD)</p>
            <p className="text-xs text-slate-500">
              {status ? <>{status.chain_ok ? "Hash-Kette intakt" : "HASH-KETTE GEBROCHEN"} · {status.records_checked} Festschreibungen geprüft · zuletzt festgeschrieben: {status.last_locked ?? "noch kein Monat"}</> : "Prüfe …"}
            </p>
          </div>
        </div>
        <a className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800" href={`/api/gobd/export?start=${from}&end=${to}`}>GoBD-Export (Z3) für die Betriebsprüfung</a>
      </div>
      <p className="mt-2 text-xs text-slate-500">Monate werden mit der Umsatzsteuer-Voranmeldung festgeschrieben (Steuern → Voranmeldung). Danach sind keine Buchungen mehr in diesem Monat möglich; spätere Vorgänge werden mit Originalbelegdatum in die nächste offene Periode gebucht.</p>
    </Card>
  );
}
