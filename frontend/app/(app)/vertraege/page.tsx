"use client";

import { useFeedback } from "@/components/Feedback";
import Link from "next/link";
import { SignaturePanel } from "@/components/SignaturePanel";
import { TerminationInbox } from "@/components/TerminationInbox";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SignDocuments } from "@/components/SignDocuments";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Select, Table, Tabs, Textarea, inputClass } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { convertPrice, rateOf } from "@/lib/decimal";
import { api, formatDate, HttpError, todayIso } from "@/lib/http";
import { TAX_CASES, UNITS, type Customer, type Product } from "@/lib/types";

interface Line { product_id: string | null; description: string; quantity: string; unit_code: string; unit_price: string; tax_case: string }
interface Contract {
  id: string; number: string; customer_id: string; customer_name: string; customer_type: "b2b" | "b2c"; title: string;
  status: "draft" | "active" | "ended"; start_date: string; min_term_months: number; notice_months: number;
  renewal: "none" | "monthly" | "term"; billing_interval: "monthly" | "quarterly" | "yearly"; billing_mode: "advance" | "arrears";
  auto_issue: boolean; end_date: string | null; notice_received: string | null; notes: string | null; term_end: string | null;
  amount_net: string; mrr: string; lines: Line[];
  billings: { period_start: string; period_end: string; invoice_id: string; number: string | null; status: string }[];
  schedule?: { start: string; end: string; factor: string; bill_on: string; billed: boolean }[];
}

const INTERVAL = { monthly: "monatlich", quarterly: "vierteljährlich", yearly: "jährlich" } as const;
const RENEWAL = { none: "endet nach Mindestlaufzeit", monthly: "danach monatlich kündbar", term: "verlängert sich um die Mindestlaufzeit" } as const;

type Form = Omit<Contract, "id" | "number" | "customer_name" | "customer_type" | "status" | "end_date" | "notice_received" | "term_end" | "amount_net" | "mrr" | "billings" | "schedule"> & { id: string | null };

const emptyForm = (): Form => ({
  id: null, customer_id: "", title: "", start_date: todayIso(), min_term_months: 12, notice_months: 1, renewal: "monthly",
  billing_interval: "monthly", billing_mode: "advance", auto_issue: true, notes: "", lines: [],
});

const de = (v: string) => (v.includes(".") ? v.replace(/0+$/, "").replace(/\.$/, "") : v).replace(".", ",");

export default function ContractsPage() {
  const [view, setView] = useState<"customers" | "sign">("customers");
  const fb = useFeedback();
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [mrr, setMrr] = useState("0");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [detail, setDetail] = useState<Contract | null>(null);
  const [noticeDate, setNoticeDate] = useState(todayIso());
  const [error, setError] = useState<string | null>(null);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void api<{ contracts: Contract[]; mrr: string }>("GET", "/api/contracts").then((r) => { setContracts(r.contracts); setMrr(r.mrr); });
  }, []);
  useEffect(() => {
    load();
    void api<Customer[]>("GET", "/api/customers").then(setCustomers);
    void api<Product[]>("GET", "/api/products").then(setProducts);
  }, [load]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }
  const openDetail = (id: string) => act(async () => { setDetail(await api<Contract>("GET", `/api/contracts/${id}`)); setForm(null); });
  // Direktlink (z. B. aus einem übernommenen Angebot): /vertraege?id=…
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) void api<Contract>("GET", `/api/contracts/${id}`).then(setDetail).catch(() => setError("Vertrag nicht gefunden"));
  }, []);

  const customer = customers.find((c) => c.id === form?.customer_id);
  const mode = customer?.customer_type === "b2c" ? "gross" : "net";

  function addProduct(id: string) {
    const p = products.find((x) => x.id === id);
    if (!p || !form) return;
    setForm({ ...form, title: form.title || p.name, lines: [...form.lines, { product_id: p.id, description: p.name, quantity: "1", unit_code: p.unit_code, unit_price: de(convertPrice(p.price, rateOf(p.tax_case), p.price_basis, mode)), tax_case: p.tax_case }] });
  }

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    void act(async () => {
      const { id, ...rest } = form;
      const body = { ...rest, lines: rest.lines.map((l) => ({ ...l, quantity: toApiDecimal(l.quantity), unit_price: toApiDecimal(l.unit_price) })) };
      const saved = id ? await api<Contract>("PUT", `/api/contracts/${id}`, body) : await api<Contract>("POST", "/api/contracts", body);
      setForm(null); load(); await openDetail(saved.id);
    });
  };

  const activate = () => detail && act(async () => {
    if (!await fb.confirm("Vertrag aktivieren? Ab dann wird automatisch abgerechnet (erste Rechnung als Entwurf zur Prüfung).")) return;
    await api("POST", `/api/contracts/${detail.id}/activate`); load(); setDetail(await api<Contract>("GET", `/api/contracts/${detail.id}`));
  });
  const terminate = () => detail && act(async () => {
    const r = await api<Contract & { earliest_end: string }>("POST", `/api/contracts/${detail.id}/terminate`, { notice_received: noticeDate });
    setNotice(`Kündigung erfasst. Vertragsende: ${formatDate(r.end_date)} (frühestmöglich ${formatDate(r.earliest_end)}).`);
    load(); setDetail(await api<Contract>("GET", `/api/contracts/${detail.id}`));
  });
  const runBilling = () => act(async () => {
    const r = await api<{ created: { number: string | null; status: string }[]; errors: { error: string }[]; skipped: boolean }>("POST", "/api/contracts/run-billing");
    setNotice(r.skipped ? "Abrechnungslauf läuft bereits." : `Abrechnungslauf: ${r.created.length} Rechnung(en) erstellt${r.errors.length ? `, ${r.errors.length} Fehler` : ""}.`);
    load(); if (detail) setDetail(await api<Contract>("GET", `/api/contracts/${detail.id}`));
  });

  return (
    <>
      <PageHeader
        title="Verträge"
        description="Softwaremiete mit Laufzeit, Kündigungsfrist und automatischer Abrechnung. Erste Rechnung je Vertrag als Entwurf, danach automatisch."
        actions={<><Button onClick={runBilling} disabled={busy}>Abrechnung jetzt ausführen</Button><Button variant="primary" onClick={() => { setDetail(null); setForm(emptyForm()); }}>+ Vertrag</Button></>}
      />
      <div className="mb-4"><Tabs value={view} onChange={setView} items={[{ value: "customers", label: "Kundenverträge" }, { value: "sign", label: "Sonstige Verträge & Signatur" }]} /></div>
      {view === "sign" ? <SignDocuments /> : <>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <TerminationInbox contracts={contracts} onChange={load} />

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Card><p className="text-xs text-slate-500">MRR (monatlich wiederkehrend, netto)</p><p className="mt-1 text-xl font-semibold tabular">{formatEuro(mrr)}</p></Card>
        <Card><p className="text-xs text-slate-500">ARR (jährlich)</p><p className="mt-1 text-xl font-semibold tabular">{formatEuro((Number(mrr) * 12).toFixed(2))}</p></Card>
        <Card><p className="text-xs text-slate-500">Aktive Verträge</p><p className="mt-1 text-xl font-semibold tabular">{contracts.filter((c) => c.status === "active").length}</p></Card>
      </div>

      {form && (
        <Card className="mb-6">
          <h2 className="mb-4 font-medium">{form.id ? "Vertrag bearbeiten" : "Neuer Vertrag"}</h2>
          <form onSubmit={save} className="grid gap-4 md:grid-cols-3">
            <Field label="Kunde"><Select required value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: e.target.value })}><option value="">– auswählen –</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.display_name} ({c.customer_type === "b2b" ? "Firma" : "Privat"})</option>)}</Select></Field>
            <Field label="Titel" className="md:col-span-2"><Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
            <Field label="Vertragsbeginn"><Input type="date" required value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></Field>
            <Field label="Mindestlaufzeit (Monate)" hint={customer?.customer_type === "b2c" ? "Verbraucher: max. 24" : undefined}><Input type="number" min={0} max={120} value={form.min_term_months} onChange={(e) => setForm({ ...form, min_term_months: Number(e.target.value) })} /></Field>
            <Field label="Kündigungsfrist (Monate, zum Monatsende)" hint={customer?.customer_type === "b2c" ? "Verbraucher: max. 1" : undefined}><Input type="number" min={0} max={24} value={form.notice_months} onChange={(e) => setForm({ ...form, notice_months: Number(e.target.value) })} /></Field>
            <Field label="Nach der Mindestlaufzeit"><Select value={form.renewal} onChange={(e) => setForm({ ...form, renewal: e.target.value as Form["renewal"] })}>{Object.entries(RENEWAL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="Abrechnung"><Select value={form.billing_interval} onChange={(e) => setForm({ ...form, billing_interval: e.target.value as Form["billing_interval"] })}>{Object.entries(INTERVAL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="Zeitpunkt"><Select value={form.billing_mode} onChange={(e) => setForm({ ...form, billing_mode: e.target.value as Form["billing_mode"] })}><option value="advance">im Voraus</option><option value="arrears">nachträglich</option></Select></Field>
            <label className="flex items-center gap-2 text-sm md:col-span-3"><input type="checkbox" checked={form.auto_issue} onChange={(e) => setForm({ ...form, auto_issue: e.target.checked })} /> Folgerechnungen automatisch ausstellen und festschreiben</label>
            <div className="md:col-span-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">Positionen je Abrechnungszeitraum ({mode === "gross" ? "brutto" : "netto"})</span>
                <select aria-label="Produkt hinzufügen" className={`${inputClass} w-full sm:w-64`} value="" disabled={!customer} onChange={(e) => addProduct(e.target.value)}><option value="">+ Produkt …</option>{products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
              </div>
              {form.lines.map((l, i) => (
                <div key={i} className="mb-2 grid grid-cols-12 gap-2">
                  <Input aria-label="Bezeichnung" className="col-span-5" value={l.description} onChange={(e) => setForm({ ...form, lines: form.lines.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)) })} />
                  <Input aria-label="Menge" className="col-span-1 text-right" value={l.quantity} onChange={(e) => setForm({ ...form, lines: form.lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)) })} />
                  <Select aria-label="Einheit" className="col-span-2" value={l.unit_code} onChange={(e) => setForm({ ...form, lines: form.lines.map((x, j) => (j === i ? { ...x, unit_code: e.target.value } : x)) })}>{UNITS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</Select>
                  <Input aria-label="Preis" className="col-span-2 text-right" value={l.unit_price} onChange={(e) => setForm({ ...form, lines: form.lines.map((x, j) => (j === i ? { ...x, unit_price: e.target.value } : x)) })} />
                  <Select aria-label="USt" className="col-span-1" value={l.tax_case} onChange={(e) => setForm({ ...form, lines: form.lines.map((x, j) => (j === i ? { ...x, tax_case: e.target.value } : x)) })}>{TAX_CASES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</Select>
                  <button type="button" aria-label="Position entfernen" className="col-span-1 text-slate-400 hover:text-red-600" onClick={() => setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })}>✕</button>
                </div>
              ))}
            </div>
            <Field label="Notizen" className="md:col-span-3"><Textarea value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
            <div className="flex gap-2 md:col-span-3"><Button type="submit" variant="primary" disabled={busy || form.lines.length === 0}>Speichern</Button><Button onClick={() => setForm(null)}>Abbrechen</Button></div>
          </form>
        </Card>
      )}

      {detail && (
        <Card className="mb-6">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="font-medium">{detail.number} · {detail.title}</h2>
              <p className="text-sm text-slate-500">{detail.customer_name} · ab {formatDate(detail.start_date)} · {INTERVAL[detail.billing_interval]} {detail.billing_mode === "advance" ? "im Voraus" : "nachträglich"} · {formatEuro(detail.amount_net)} netto je Zeitraum</p>
              <p className="text-sm text-slate-500">Mindestlaufzeit bis {formatDate(detail.term_end)} · Kündigungsfrist {detail.notice_months} Monat(e) · {RENEWAL[detail.renewal]}{detail.end_date ? ` · endet am ${formatDate(detail.end_date)}` : ""}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {detail.status === "draft" && <><Button onClick={() => setForm({ ...detail, lines: detail.lines.map((l) => ({ ...l, quantity: de(l.quantity), unit_price: de(l.unit_price) })) })}>Bearbeiten</Button><Button variant="primary" onClick={activate} disabled={busy}>Aktivieren</Button></>}
              {detail.status === "active" && !detail.notice_received && (
                <>
                  <Input type="date" aria-label="Kündigung eingegangen am" className="w-40" value={noticeDate} onChange={(e) => setNoticeDate(e.target.value)} />
                  <Button variant="danger" onClick={terminate} disabled={busy}>Kündigung erfassen</Button>
                </>
              )}
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-medium text-slate-500">Abgerechnet</h3>
              {detail.billings.length === 0 ? <p className="text-sm text-slate-500">Noch keine Rechnungen.</p> : (
                <ul className="space-y-1 text-sm">{detail.billings.map((b) => <li key={b.period_start}>{formatDate(b.period_start)} – {formatDate(b.period_end)}: <Link className="text-brand-700 hover:underline dark:text-brand-100" href={`/rechnungen/${b.invoice_id}`}>{b.number ?? "Entwurf"}</Link></li>)}</ul>
              )}
            </div>
            <div>
              <h3 className="mb-2 text-sm font-medium text-slate-500">Nächste Zeiträume</h3>
              <ul className="space-y-1 text-sm">{(detail.schedule ?? []).filter((s) => !s.billed).slice(0, 6).map((s) => <li key={s.start}>{formatDate(s.start)} – {formatDate(s.end)} · Rechnung am {formatDate(s.bill_on)}{s.factor !== "1" ? ` · anteilig ${(Number(s.factor) * 100).toFixed(2).replace(".", ",")} %` : ""}</li>)}</ul>
            </div>
          </div>
        </Card>
      )}
      {detail && (
        <SignaturePanel key={detail.id} subjectType="contract" subjectId={detail.id} canRequest={detail.status === "draft"}
          defaultName={detail.customer_type === "b2c" ? detail.customer_name : ""} defaultEmail=""
          onChange={async () => { load(); setDetail(await api<Contract>("GET", `/api/contracts/${detail.id}`)); }} />
      )}

      <Card>
        {contracts.length === 0 ? <Empty>Noch keine Verträge.</Empty> : (
          <Table head={<tr><th className="pb-2 font-medium">Nr.</th><th className="pb-2 font-medium">Kunde</th><th className="pb-2 font-medium">Titel</th><th className="pb-2 font-medium">Abrechnung</th><th className="pb-2 text-right font-medium">MRR</th><th className="pb-2 font-medium">Status</th></tr>}>
            {contracts.map((c) => (
              <tr key={c.id} className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50" onClick={() => openDetail(c.id)}>
                <td className="py-2 tabular text-brand-700 dark:text-brand-100">{c.number}</td>
                <td className="py-2">{c.customer_name}</td>
                <td className="py-2">{c.title}</td>
                <td className="py-2">{INTERVAL[c.billing_interval]}</td>
                <td className="py-2 text-right tabular">{formatEuro(c.mrr)}</td>
                <td className="py-2">{c.status === "draft" ? <Badge>Entwurf</Badge> : c.status === "ended" ? <Badge>Beendet</Badge> : c.notice_received ? <Badge tone="amber">Gekündigt</Badge> : <Badge tone="green">Aktiv</Badge>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      </>}
    </>
  );
}
