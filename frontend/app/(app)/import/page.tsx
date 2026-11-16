"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Empty, Field, Input, PageHeader, PageSkeleton, Select, Table, Tabs } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError, upload } from "@/lib/http";

interface Account { number: string; name: string; type: string }
interface Item { id?: string; kind: "receivable" | "payable"; party: string; document_number: string; document_date: string; due_date: string | null; amount: string; paid_on?: string | null }
interface Opening { chart: string; entry: { id: string; entry_date: string; number: string } | null; lines: { account: string; debit: string; credit: string; note: string | null }[]; items: Item[]; accounts: Account[] }
interface History { id: string; kind: string; filename: string | null; rows: number; created_by: string; created_at: string }
interface DatevResult { summary: { rows: number; total: string; new_accounts: string[]; errors: number; period: string }; preview: { line: number; date: string; debit: string; credit: string; amount: string; vat: boolean; text: string; document: string | null }[]; errors: string[] }
interface PartyResult { summary: { new: number; skipped_existing: number; errors: number }; preview: string[]; errors: string[] }

const KIND_LABEL: Record<string, string> = { datev: "DATEV-Buchungen", customers: "Kunden", suppliers: "Lieferanten", opening: "Eröffnungsbilanz" };

export default function ImportPage() {
  const [tab, setTab] = useState<"opening" | "datev" | "parties" | "history">("opening");
  const [data, setData] = useState<{ opening: Opening; history: History[] } | null>(null);
  const load = useCallback(() => { void api<{ opening: Opening; history: History[] }>("GET", "/api/import").then(setData); }, []);
  useEffect(load, [load]);
  if (!data) return <PageSkeleton stats={0} />;
  return (
    <>
      <PageHeader title="Umstieg & Import" eyebrow="System"
        description="Von Lexoffice, sevDesk, DATEV oder Excel wechseln: Eröffnungsbilanz und offene Posten übernehmen, Buchungsstapel und Stammdaten importieren. Immer erst Vorschau, dann Übernahme per Klick." />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[
        { value: "opening", label: "Eröffnungsbilanz" }, { value: "datev", label: "DATEV-Buchungen" },
        { value: "parties", label: "Kunden & Lieferanten" }, { value: "history", label: `Protokoll (${data.history.length})` },
      ]} /></div>
      {tab === "opening" && <OpeningTab opening={data.opening} reload={load} />}
      {tab === "datev" && <DatevTab reload={load} />}
      {tab === "parties" && <PartiesTab reload={load} />}
      {tab === "history" && (
        <Card>{data.history.length === 0 ? <Empty>Noch keine Importe.</Empty> : (
          <Table head={<tr><th>Datum</th><th>Art</th><th>Datei</th><th className="text-right">Datensätze</th><th>von</th></tr>}>
            {data.history.map((h) => <tr key={h.id}><td className="tabular">{formatDate(h.created_at)}</td><td>{KIND_LABEL[h.kind] ?? h.kind}</td><td className="max-w-[16rem] truncate">{h.filename ?? "–"}</td><td className="text-right tabular">{h.rows}</td><td className="text-xs text-slate-500">{h.created_by}</td></tr>)}
          </Table>
        )}</Card>
      )}
    </>
  );
}

/* ───────────────────────── Eröffnungsbilanz ───────────────────────── */

function OpeningTab({ opening, reload }: { opening: Opening; reload: () => void }) {
  const [date, setDate] = useState(`${new Date().getFullYear()}-01-01`);
  const [rows, setRows] = useState<{ account: string; debit: string; credit: string }[]>([{ account: "", debit: "", credit: "" }]);
  const [items, setItems] = useState<Item[]>([]);
  const [csvErrors, setCsvErrors] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm, prompt } = useFeedback();
  const accName = useMemo(() => Object.fromEntries(opening.accounts.map((a) => [a.number, a.name])), [opening.accounts]);

  const num = (v: string) => Number(toApiDecimal(v || "0")) || 0;
  const debit = rows.reduce((s, r) => s + num(r.debit), 0) + items.filter((i) => i.kind === "receivable").reduce((s, i) => s + Number(i.amount), 0);
  const credit = rows.reduce((s, r) => s + num(r.credit), 0) + items.filter((i) => i.kind === "payable").reduce((s, i) => s + Number(i.amount), 0);
  const diff = Math.round((debit - credit) * 100) / 100;

  if (opening.entry) {
    return (
      <div className="grid gap-6">
        <Alert kind="success">Eröffnungsbilanz zum {formatDate(opening.entry.entry_date)} gebucht ({opening.entry.number}). Korrekturen bitte als manuelle Buchung in der Buchhaltung.</Alert>
        <Card>
          <CardHeader title="Saldenvorträge" />
          <Table head={<tr><th>Konto</th><th className="text-right">Soll</th><th className="text-right">Haben</th></tr>}>
            {opening.lines.filter((l) => !l.account.startsWith("900")).map((l, i) => <tr key={i}><td><span className="tabular">{l.account}</span> <span className="text-slate-500">{accName[l.account] ?? l.note ?? ""}</span></td><td className="text-right tabular">{Number(l.debit) ? formatEuro(l.debit) : ""}</td><td className="text-right tabular">{Number(l.credit) ? formatEuro(l.credit) : ""}</td></tr>)}
          </Table>
        </Card>
        {opening.items.length > 0 && (
          <Card>
            <CardHeader title="Offene Posten aus dem Altsystem" description="Bei Zahlungseingang bzw. -ausgang hier als bezahlt buchen." />
            <Table head={<tr><th>Art</th><th>Partner</th><th>Beleg</th><th>Fällig</th><th className="text-right">Betrag</th><th /></tr>}>
              {opening.items.map((i) => (
                <tr key={i.id}>
                  <td><Badge tone={i.kind === "receivable" ? "blue" : "amber"}>{i.kind === "receivable" ? "Forderung" : "Verbindlichkeit"}</Badge></td>
                  <td>{i.party}</td><td className="tabular">{i.document_number}<span className="block text-xs text-slate-500">{formatDate(i.document_date)}</span></td>
                  <td className="tabular">{formatDate(i.due_date)}</td><td className="text-right tabular">{formatEuro(i.amount)}</td>
                  <td className="text-right">{i.paid_on ? <span className="text-xs text-slate-500">bezahlt {formatDate(i.paid_on)}</span> : (
                    <Button className="!h-7 !text-xs" onClick={async () => {
                      const d = await prompt({ title: "Zahlung buchen", confirmLabel: "Buchen", message: `${i.document_number} · ${formatEuro(i.amount)}`, input: { label: "Zahlungsdatum (TT.MM.JJJJ)" } });
                      const m = d?.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
                      if (!d) return;
                      if (!m) { toast("Datum als TT.MM.JJJJ eingeben.", "error"); return; }
                      try { await api("POST", `/api/import/opening/items/${i.id}/paid`, { paid_on: `${m[3]}-${m[2]!.padStart(2, "0")}-${m[1]!.padStart(2, "0")}` }); toast("Zahlung gebucht."); reload(); }
                      catch (e) { toast(e instanceof HttpError ? e.message : "Buchen fehlgeschlagen", "error"); }
                    }}>Bezahlt</Button>
                  )}</td>
                </tr>
              ))}
            </Table>
          </Card>
        )}
      </div>
    );
  }

  const readCsv = async (f: File) => {
    try { const r = await upload<{ items: Item[]; errors: string[] }>("/api/import/opening/items-csv", f); setItems(r.items); setCsvErrors(r.errors); toast(`${r.items.length} offene Posten gelesen.`); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Datei konnte nicht gelesen werden", "error"); }
  };
  const submit = async () => {
    setError(null);
    if (!(await confirm({ title: `Eröffnungsbilanz zum ${formatDate(date)} buchen?`, confirmLabel: "Buchen und festschreiben",
      message: "Die Saldenvorträge werden gegen 9000/9008/9009 gebucht und festgeschrieben. Das geht nur einmal." }))) return;
    setBusy(true);
    try {
      await api("POST", "/api/import/opening", { opening_date: date,
        balances: rows.filter((r) => r.account && (num(r.debit) || num(r.credit))).map((r) => ({ account: r.account, debit: toApiDecimal(r.debit || "0"), credit: toApiDecimal(r.credit || "0") })),
        items: items.map(({ kind, party, document_number, document_date, due_date, amount }) => ({ kind, party, document_number, document_date, due_date, amount })) });
      toast("Eröffnungsbilanz gebucht."); reload();
    } catch (e) { setError(e instanceof HttpError ? e.message : "Buchen fehlgeschlagen"); } finally { setBusy(false); }
  };

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader title="Bestandskonten" description="Schlusssalden aus der Bilanz des Vorjahres bzw. vom Umstiegstag (Summen- und Saldenliste). Aktiva im Soll, Passiva und Eigenkapital im Haben." />
        <div className="mb-4 max-w-xs"><Field label="Stichtag (Eröffnung)"><Input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field></div>
        <div className="grid gap-2">
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-[minmax(0,1fr)_7rem_7rem_2rem] items-center gap-2">
              <Select aria-label="Konto" value={r.account} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, account: e.target.value } : x)))}>
                <option value="">Konto wählen …</option>
                {opening.accounts.map((a) => <option key={a.number} value={a.number}>{a.number} {a.name}</option>)}
              </Select>
              <Input aria-label="Soll" inputMode="decimal" placeholder="Soll" value={r.debit} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, debit: e.target.value, credit: e.target.value ? "" : x.credit } : x)))} />
              <Input aria-label="Haben" inputMode="decimal" placeholder="Haben" value={r.credit} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, credit: e.target.value, debit: e.target.value ? "" : x.debit } : x)))} />
              <button type="button" aria-label="Zeile entfernen" className="text-slate-400 hover:text-red-600" onClick={() => setRows(rows.length > 1 ? rows.filter((_, j) => j !== i) : [{ account: "", debit: "", credit: "" }])}>×</button>
            </div>
          ))}
          <div><Button variant="ghost" className="!h-8 !text-xs" onClick={() => setRows([...rows, { account: "", debit: "", credit: "" }])}>+ Konto</Button></div>
        </div>
      </Card>
      <Card>
        <CardHeader title="Offene Posten" description="Unbezahlte Ausgangs- und Eingangsrechnungen am Stichtag – laufen über das Forderungs- bzw. Verbindlichkeitenkonto und lassen sich später einzeln als bezahlt buchen."
          actions={<label className="inline-flex h-8 cursor-pointer items-center rounded-lg px-3 text-xs font-medium shadow-[0_0_0_1px_rgb(15_15_25/0.12)] hover:bg-slate-50 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.1)] dark:hover:bg-white/5">
            CSV einlesen<input type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) void readCsv(f); e.target.value = ""; }} />
          </label>} />
        <p className="mb-3 text-xs text-slate-500">Spalten: <code>Art;Name;Belegnummer;Belegdatum;Fällig;Betrag</code> – Art „Forderung“ oder „Verbindlichkeit“, Beträge brutto.</p>
        {csvErrors.length > 0 && <div className="mb-3"><Alert kind="warning">{csvErrors.slice(0, 5).join(" · ")}</Alert></div>}
        {items.length === 0 ? <Empty>Keine offenen Posten erfasst.</Empty> : (
          <Table head={<tr><th>Art</th><th>Partner</th><th>Beleg</th><th className="text-right">Betrag</th></tr>}>
            {items.map((i) => <tr key={`${i.kind}${i.document_number}`}><td>{i.kind === "receivable" ? "Forderung" : "Verbindlichkeit"}</td><td>{i.party}</td><td className="tabular">{i.document_number} · {formatDate(i.document_date)}</td><td className="text-right tabular">{formatEuro(i.amount)}</td></tr>)}
          </Table>
        )}
      </Card>
      <Card>
        <div className="flex flex-wrap items-center gap-4">
          <div className="text-sm tabular">Soll {formatEuro(debit)} · Haben {formatEuro(credit)}</div>
          {diff === 0 ? <Badge tone="green">ausgeglichen</Badge> : <Badge tone="red">Differenz {formatEuro(Math.abs(diff))}</Badge>}
          <Button variant="primary" className="ml-auto" disabled={busy || diff !== 0 || debit === 0} onClick={() => void submit()}>Eröffnungsbilanz buchen</Button>
        </div>
        {error && <div className="mt-3"><Alert>{error}</Alert></div>}
      </Card>
    </div>
  );
}

/* ───────────────────────── DATEV ───────────────────────── */

function DatevTab({ reload }: { reload: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<DatevResult | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();
  const run = async (f: File, dry: boolean) => {
    setBusy(true);
    try {
      const r = await upload<DatevResult>("/api/import/datev", f, { dry_run: String(dry) });
      if (dry) setResult(r); else { toast(`${r.summary.rows} Buchungen übernommen und festgeschrieben.`); setResult(null); setFile(null); reload(); }
    } catch (e) { toast(e instanceof HttpError ? e.message : "Import fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader title="DATEV-Buchungsstapel" description="Export „Buchungsstapel“ im DATEV-Format (EXTF, .csv) aus DATEV, Lexoffice, sevDesk, Agenda oder dem Steuerbüro. BU-Schlüssel 2/3/8/9 und Automatikkonten werden in Netto und Steuer aufgeteilt, Debitoren/Kreditoren auf die Sammelkonten gebucht." />
        <label className="inline-flex h-9 cursor-pointer items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700">
          Datei wählen …<input type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); void run(f, true); } e.target.value = ""; }} />
        </label>
        {file && <span className="ml-3 text-sm text-slate-500">{file.name}</span>}
      </Card>
      {result && file && (
        <Card>
          <CardHeader title={`Vorschau: ${result.summary.rows} Buchungen`} description={`Zeitraum ${result.summary.period} · Summe ${formatEuro(result.summary.total)}`}
            actions={<Button variant="primary" disabled={busy || result.errors.length > 0 || result.summary.rows === 0} onClick={async () => {
              if (await confirm({ title: `${result.summary.rows} Buchungen übernehmen?`, confirmLabel: "Übernehmen", message: "Die Buchungen werden festgeschrieben. Die gleiche Datei lässt sich nicht zweimal importieren." })) void run(file, false);
            }}>Übernehmen</Button>} />
          {result.errors.length > 0 && <div className="mb-3"><Alert>{result.errors.length} fehlerhafte Zeilen – bitte korrigieren: {result.errors.slice(0, 5).join(" · ")}</Alert></div>}
          {result.summary.new_accounts.length > 0 && <div className="mb-3"><Alert kind="info">Neue Konten werden angelegt: {result.summary.new_accounts.join(", ")}</Alert></div>}
          <Table head={<tr><th>Datum</th><th>Beleg</th><th>Text</th><th>Soll</th><th>Haben</th><th className="text-right">Betrag</th></tr>}>
            {result.preview.map((p) => <tr key={p.line}><td className="tabular">{formatDate(p.date)}</td><td className="tabular">{p.document ?? "–"}</td><td>{p.text}{p.vat && <span className="ml-1 text-xs text-slate-500">(mit USt)</span>}</td><td className="tabular">{p.debit}</td><td className="tabular">{p.credit}</td><td className="text-right tabular">{formatEuro(p.amount)}</td></tr>)}
          </Table>
          {result.summary.rows > result.preview.length && <p className="mt-2 text-xs text-slate-500">… und {result.summary.rows - result.preview.length} weitere</p>}
        </Card>
      )}
    </div>
  );
}

/* ───────────────────────── Kunden & Lieferanten ───────────────────────── */

function PartiesTab({ reload }: { reload: () => void }) {
  const [kind, setKind] = useState<"customers" | "suppliers">("customers");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<PartyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast } = useFeedback();
  const run = async (f: File, dry: boolean) => {
    setBusy(true);
    try {
      const r = await upload<PartyResult>(`/api/import/${kind}`, f, { dry_run: String(dry) });
      if (dry) setResult(r); else { toast(`${r.summary.new} ${kind === "customers" ? "Kunden" : "Lieferanten"} angelegt.`); setResult(null); setFile(null); reload(); }
    } catch (e) { toast(e instanceof HttpError ? e.message : "Import fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader title="Stammdaten aus CSV" description="Kontakt-Export aus Lexoffice, sevDesk oder Excel. Spalten werden automatisch erkannt (Firmenname, Vorname, Nachname, E-Mail, Straße, PLZ, Ort, USt-IdNr.). Vorhandene Namen werden übersprungen." />
        <div className="flex flex-wrap items-center gap-3">
          <Select aria-label="Art" value={kind} onChange={(e) => { setKind(e.target.value as typeof kind); setResult(null); setFile(null); }} className="w-44"><option value="customers">Kunden</option><option value="suppliers">Lieferanten</option></Select>
          <label className="inline-flex h-9 cursor-pointer items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700">
            CSV wählen …<input type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); void run(f, true); } e.target.value = ""; }} />
          </label>
          {file && <span className="text-sm text-slate-500">{file.name}</span>}
        </div>
        {kind === "suppliers" && <p className="mt-3 text-xs text-slate-500">Importierte Lieferanten erscheinen unter Belege mit „bitte prüfen“, bis ein Standardkonto festgelegt ist.</p>}
      </Card>
      {result && file && (
        <Card>
          <CardHeader title={`Vorschau: ${result.summary.new} neu`} description={`${result.summary.skipped_existing} bereits vorhanden · ${result.summary.errors} fehlerhaft`}
            actions={<Button variant="primary" disabled={busy || result.summary.new === 0} onClick={() => void run(file, false)}>{result.summary.new} übernehmen</Button>} />
          {result.errors.length > 0 && <div className="mb-3"><Alert kind="warning"><ul className="list-disc pl-4">{result.errors.slice(0, 8).map((e) => <li key={e}>{e}</li>)}</ul>{result.errors.length > 8 && `… und ${result.errors.length - 8} weitere`}</Alert></div>}
          <ul className="columns-1 gap-6 text-sm sm:columns-2">{result.preview.map((p) => <li key={p} className="py-0.5">{p}</li>)}</ul>
        </Card>
      )}
    </div>
  );
}
