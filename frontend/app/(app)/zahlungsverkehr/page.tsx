"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Table, Tabs } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

interface TransferRow { id: string; supplier_id: string; supplier_name: string; invoice_number: string | null; description: string | null; receipt_date: string; payable: string; iban: string | null; in_file_at: string | null }
interface DebitRow { invoice_id: string; number: string; customer_name: string; due_date: string; amount: string; mandate_reference: string; sequence: string; in_file_at: string | null }
interface Mandate { id: string; customer_id: string; customer_name: string; reference: string; scheme: string; signed_on: string; account_holder: string; iban: string; recurring: boolean; first_used_on: string | null; last_used_on: string | null; revoked_on: string | null }
interface FileRow { id: string; kind: string; purpose: string; msg_id: string; due_date: string; count: number; total: string; created_by: string; created_at: string }
interface Data { settings: { creditor_id: string | null; batch_booking: boolean }; transfers: TransferRow[]; debits: DebitRow[]; mandates: Mandate[]; files: FileRow[]; earliest_collection: string; next_execution: string }
interface Customer { id: string; display_name: string; number: string }

const PURPOSE: Record<string, string> = { suppliers: "Lieferanten", salaries: "Löhne", customers: "Lastschrift" };
const SEQ: Record<string, string> = { FRST: "Erstlastschrift", RCUR: "Folgelastschrift", OOFF: "einmalig" };
const maskIban = (v: string | null) => (v ? `${v.slice(0, 4)} •••• ${v.slice(-4)}` : null);

export default function PaymentsPage() {
  const [tab, setTab] = useState<"transfers" | "debits" | "mandates" | "files">("transfers");
  const [data, setData] = useState<Data | null>(null);
  const load = useCallback(() => { void api<Data>("GET", "/api/sepa").then(setData); }, []);
  useEffect(load, [load]);
  if (!data) return <PageSkeleton stats={0} />;
  const active = data.mandates.filter((m) => !m.revoked_on).length;
  return (
    <>
      <PageHeader title="Zahlungsverkehr" eyebrow="Finanzen"
        description="SEPA-Dateien für Überweisungen und Lastschriften erzeugen und im Online-Banking hochladen. Die Zahlungen werden beim Kontoauszug-Import automatisch zugeordnet." />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[
        { value: "transfers", label: `Überweisungen (${data.transfers.length})` }, { value: "debits", label: `Lastschriften (${data.debits.length})` },
        { value: "mandates", label: `Mandate (${active})` }, { value: "files", label: "Dateien" },
      ]} /></div>
      {tab === "transfers" && <Transfers data={data} reload={load} />}
      {tab === "debits" && <Debits data={data} reload={load} />}
      {tab === "mandates" && <Mandates data={data} reload={load} />}
      {tab === "files" && (
        <Card>{data.files.length === 0 ? <Empty>Noch keine Zahlungsdateien erzeugt.</Empty> : (
          <Table head={<tr><th>Erstellt</th><th>Art</th><th>Ausführung</th><th className="text-right">Posten</th><th className="text-right">Summe</th><th /></tr>}>
            {data.files.map((f) => (
              <tr key={f.id}>
                <td className="tabular">{formatDate(f.created_at)}<span className="block text-xs text-slate-500">{f.created_by}</span></td>
                <td>{f.kind === "transfer" ? "Überweisung" : "Lastschrift"} · {PURPOSE[f.purpose]}</td>
                <td className="tabular">{formatDate(f.due_date)}</td><td className="text-right tabular">{f.count}</td>
                <td className="text-right tabular">{formatEuro(f.total)}</td>
                <td className="text-right"><a className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-300" href={`/api/sepa/files/${f.id}.xml`}>XML</a></td>
              </tr>
            ))}
          </Table>
        )}</Card>
      )}
    </>
  );
}

function useSelection<T extends string>() {
  const [sel, setSel] = useState<Set<T>>(new Set());
  const toggle = (id: T) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return { sel, setSel, toggle };
}

function Transfers({ data, reload }: { data: Data; reload: () => void }) {
  const { sel, setSel, toggle } = useSelection<string>();
  const [date, setDate] = useState(data.next_execution);
  const [busy, setBusy] = useState(false);
  const { toast } = useFeedback();
  const ready = data.transfers.filter((t) => t.iban);
  const total = data.transfers.filter((t) => sel.has(t.id)).reduce((s, t) => s + Number(t.payable), 0);
  const create = async () => {
    setBusy(true);
    try {
      const r = await api<{ id: string }>("POST", "/api/sepa/transfers", { receipt_ids: [...sel], execution_date: date });
      window.location.href = `/api/sepa/files/${r.id}.xml`;
      toast(`Überweisungsdatei mit ${sel.size} Posten erzeugt – jetzt im Online-Banking hochladen.`); setSel(new Set()); reload();
    } catch (e) { toast(e instanceof HttpError ? e.message : "Erzeugen fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  return (
    <Card>
      <CardHeader title="Offene Eingangsrechnungen" description="Gebuchte, noch nicht bezahlte Belege. IBAN des Lieferanten unter Belege → Lieferanten pflegen. Löhne: Mitarbeiter & Lohn → Lohnlauf."
        actions={<div className="flex flex-wrap items-center gap-2">
          <Input type="date" aria-label="Ausführungsdatum" value={date} onChange={(e) => setDate(e.target.value)} className="!w-40" />
          <Button variant="primary" disabled={busy || sel.size === 0} onClick={() => void create()}>{sel.size ? `${sel.size} überweisen · ${formatEuro(total)}` : "Auswählen …"}</Button>
        </div>} />
      {data.transfers.length === 0 ? <Empty>Keine offenen Eingangsrechnungen.</Empty> : (
        <Table head={<tr><th className="w-8"><input type="checkbox" aria-label="Alle mit IBAN auswählen" checked={ready.length > 0 && ready.every((t) => sel.has(t.id))} onChange={(e) => setSel(e.target.checked ? new Set(ready.map((t) => t.id)) : new Set())} /></th><th>Lieferant</th><th>Rechnung</th><th>IBAN</th><th className="text-right">Betrag</th></tr>}>
          {data.transfers.map((t) => (
            <tr key={t.id}>
              <td><input type="checkbox" aria-label={`${t.supplier_name} auswählen`} disabled={!t.iban} checked={sel.has(t.id)} onChange={() => toggle(t.id)} /></td>
              <td className="font-medium">{t.supplier_name}{t.in_file_at && <span className="block text-xs font-normal text-amber-700 dark:text-amber-400">bereits in Datei vom {formatDate(t.in_file_at)}</span>}</td>
              <td>{t.invoice_number ?? t.description ?? "–"}<span className="block text-xs text-slate-500">{formatDate(t.receipt_date)}</span></td>
              <td className="tabular text-xs">{maskIban(t.iban) ?? <span className="text-red-600">fehlt</span>}</td>
              <td className="text-right tabular">{formatEuro(t.payable)}</td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

function Debits({ data, reload }: { data: Data; reload: () => void }) {
  const { sel, setSel, toggle } = useSelection<string>();
  const [date, setDate] = useState(data.earliest_collection);
  const [cid, setCid] = useState(data.settings.creditor_id ?? "");
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();
  const total = data.debits.filter((d) => sel.has(d.invoice_id)).reduce((s, d) => s + Number(d.amount), 0);
  const saveCid = async () => {
    try { await api("PUT", "/api/sepa/settings", { creditor_id: cid || null, batch_booking: data.settings.batch_booking }); toast("Gläubiger-ID gespeichert."); reload(); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Speichern fehlgeschlagen", "error"); }
  };
  const create = async () => {
    if (!(await confirm({ title: `${sel.size} Lastschriften über ${formatEuro(total)} erzeugen?`, confirmLabel: "Datei erzeugen",
      message: <>Fälligkeit {formatDate(date)}. Bitte die Kunden vorab informieren (Pre-Notification, i. d. R. auf der Rechnung). Erstlastschriften werden danach zu Folgelastschriften.</> }))) return;
    setBusy(true);
    try {
      const r = await api<{ id: string }>("POST", "/api/sepa/debits", { invoice_ids: [...sel], collection_date: date });
      window.location.href = `/api/sepa/files/${r.id}.xml`;
      toast("Lastschriftdatei erzeugt – jetzt im Online-Banking hochladen."); setSel(new Set()); reload();
    } catch (e) { toast(e instanceof HttpError ? e.message : "Erzeugen fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  return (
    <div className="grid gap-6">
      {!data.settings.creditor_id && <Alert kind="info">Für Lastschriften wird eine Gläubiger-Identifikationsnummer der Bundesbank benötigt (kostenlos online beantragen).</Alert>}
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Gläubiger-Identifikationsnummer" className="w-72"><Input placeholder="DE98ZZZ09999999999" value={cid} onChange={(e) => setCid(e.target.value.toUpperCase())} /></Field>
          <Button onClick={() => void saveCid()}>Speichern</Button>
        </div>
      </Card>
      <Card>
        <CardHeader title="Offene Rechnungen mit Mandat" description={`Frühestmögliche Fälligkeit: ${formatDate(data.earliest_collection)} (ein Geschäftstag Vorlauf).`}
          actions={<div className="flex flex-wrap items-center gap-2">
            <Input type="date" aria-label="Fälligkeitsdatum" min={data.earliest_collection} value={date} onChange={(e) => setDate(e.target.value)} className="!w-40" />
            <Button variant="primary" disabled={busy || sel.size === 0 || !data.settings.creditor_id} onClick={() => void create()}>{sel.size ? `${sel.size} einziehen · ${formatEuro(total)}` : "Auswählen …"}</Button>
          </div>} />
        {data.debits.length === 0 ? <Empty>Keine offenen Rechnungen von Kunden mit aktivem Lastschriftmandat.</Empty> : (
          <Table head={<tr><th className="w-8"><input type="checkbox" aria-label="Alle auswählen" checked={data.debits.every((d) => sel.has(d.invoice_id))} onChange={(e) => setSel(e.target.checked ? new Set(data.debits.map((d) => d.invoice_id)) : new Set())} /></th><th>Kunde</th><th>Rechnung</th><th>Mandat</th><th className="text-right">Betrag</th></tr>}>
            {data.debits.map((d) => (
              <tr key={d.invoice_id}>
                <td><input type="checkbox" aria-label={`${d.number} auswählen`} checked={sel.has(d.invoice_id)} onChange={() => toggle(d.invoice_id)} /></td>
                <td className="font-medium">{d.customer_name}{d.in_file_at && <span className="block text-xs font-normal text-amber-700 dark:text-amber-400">bereits eingezogen am {formatDate(d.in_file_at)}</span>}</td>
                <td className="tabular">{d.number}<span className="block text-xs text-slate-500">fällig {formatDate(d.due_date)}</span></td>
                <td className="text-xs">{d.mandate_reference}<span className="block text-slate-500">{SEQ[d.sequence]}</span></td>
                <td className="text-right tabular">{formatEuro(d.amount)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function Mandates({ data, reload }: { data: Data; reload: () => void }) {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [form, setForm] = useState<{ customer_id: string; reference: string; scheme: "CORE" | "B2B"; signed_on: string; account_holder: string; iban: string; bic: string; recurring: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { toast, confirm } = useFeedback();
  useEffect(() => { void api<Customer[]>("GET", "/api/customers").then(setCustomers); }, []);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(null);
    try {
      await api("POST", "/api/sepa/mandates", { ...form, reference: form.reference.trim() || null, bic: form.bic.trim() || null });
      toast("Mandat gespeichert."); setForm(null); reload();
    } catch (err) { setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen"); }
  };
  const revoke = async (m: Mandate) => {
    if (!(await confirm({ title: `Mandat ${m.reference} widerrufen?`, tone: "danger", confirmLabel: "Widerrufen", message: `${m.customer_name} – danach keine Lastschriften mehr.` }))) return;
    try { await api("POST", `/api/sepa/mandates/${m.id}/revoke`, { revoked_on: new Date().toISOString().slice(0, 10) }); toast("Mandat widerrufen."); reload(); }
    catch (err) { toast(err instanceof HttpError ? err.message : "Widerruf fehlgeschlagen", "error"); }
  };
  return (
    <Card>
      <CardHeader title="SEPA-Lastschriftmandate" description="Unterschriebenes Mandat (Papier oder PDF) bitte im Dokumentenarchiv ablegen. Mandate verfallen nach 36 Monaten ohne Nutzung."
        actions={<Button variant="primary" onClick={() => setForm({ customer_id: customers[0]?.id ?? "", reference: "", scheme: "CORE", signed_on: new Date().toISOString().slice(0, 10), account_holder: "", iban: "", bic: "", recurring: true })}>+ Mandat</Button>} />
      {data.mandates.length === 0 ? <Empty>Noch keine Mandate.</Empty> : (
        <Table head={<tr><th>Kunde</th><th>Referenz</th><th>Konto</th><th>Unterschrieben</th><th>Status</th><th /></tr>}>
          {data.mandates.map((m) => (
            <tr key={m.id}>
              <td className="font-medium">{m.customer_name}</td>
              <td className="tabular text-xs">{m.reference}<span className="block text-slate-500">{m.scheme === "B2B" ? "Firmenlastschrift" : "Basislastschrift"}{m.recurring ? " · wiederkehrend" : " · einmalig"}</span></td>
              <td className="text-xs">{m.account_holder}<span className="block tabular text-slate-500">{maskIban(m.iban)}</span></td>
              <td className="tabular">{formatDate(m.signed_on)}</td>
              <td>{m.revoked_on ? <Badge>widerrufen {formatDate(m.revoked_on)}</Badge> : m.first_used_on ? <Badge tone="green">genutzt {formatDate(m.last_used_on)}</Badge> : <Badge tone="blue">aktiv</Badge>}</td>
              <td className="text-right">{!m.revoked_on && <Button variant="ghost" className="!h-7 !text-xs" onClick={() => void revoke(m)}>Widerrufen</Button>}</td>
            </tr>
          ))}
        </Table>
      )}
      <Drawer open={!!form} onClose={() => setForm(null)} title="Neues Lastschriftmandat" subtitle="Angaben aus dem unterschriebenen Mandat"
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="mandate-form" variant="primary">Speichern</Button></>}>
        {form && (
          <form id="mandate-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Kunde" className="sm:col-span-2"><Select required value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: e.target.value })}>{customers.map((c) => <option key={c.id} value={c.id}>{c.display_name} ({c.number})</option>)}</Select></Field>
            <Field label="Kontoinhaber"><Input required value={form.account_holder} onChange={(e) => setForm({ ...form, account_holder: e.target.value })} /></Field>
            <Field label="IBAN"><Input required value={form.iban} onChange={(e) => setForm({ ...form, iban: e.target.value })} /></Field>
            <Field label="BIC (optional)"><Input value={form.bic} onChange={(e) => setForm({ ...form, bic: e.target.value.toUpperCase() })} /></Field>
            <Field label="Unterschrieben am"><Input type="date" required value={form.signed_on} onChange={(e) => setForm({ ...form, signed_on: e.target.value })} /></Field>
            <Field label="Mandatsreferenz" hint="leer = automatisch (SL-Kundennr.-lfd.)"><Input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
            <Field label="Art"><Select value={form.scheme} onChange={(e) => setForm({ ...form, scheme: e.target.value as "CORE" | "B2B" })}><option value="CORE">Basislastschrift (CORE)</option><option value="B2B">Firmenlastschrift (B2B)</option></Select></Field>
            <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={form.recurring} onChange={(e) => setForm({ ...form, recurring: e.target.checked })} />wiederkehrende Lastschriften</label>
            {error && <div className="sm:col-span-2"><Alert>{error}</Alert></div>}
          </form>
        )}
      </Drawer>
    </Card>
  );
}
