"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { ReceiptPreview } from "@/components/ReceiptPreview";
import { Alert, Badge, Button, Card, Drawer, Empty, Field, Input, linkButtonClass, PageHeader, PageSkeleton, Select, Table, Tabs, Textarea } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError, todayIso } from "@/lib/http";

interface Meta {
  categories: { code: string; label: string; skr04: string }[];
  tax_cases: { code: string; label: string; rate: string; reverse_charge: boolean }[];
}
interface Supplier {
  id: string;
  name: string;
  country_code: string;
  vat_id: string | null;
  default_tax_case: string;
  default_category: string;
  default_account: string | null;
  needs_review: boolean;
  notes: string | null;
  iban?: string | null;
  bic?: string | null;
}
interface Account { number: string; name: string }
interface Receipt {
  id: string;
  status: "draft" | "booked";
  kind: "expense" | "reversal";
  supplier_id: string;
  supplier_name: string;
  file_id: string | null;
  receipt_date: string;
  service_date: string | null;
  invoice_number: string | null;
  description: string;
  category: string;
  tax_case: string;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
  rc_tax_amount: string;
  input_tax_amount: string;
  expense_amount: string;
  deductible_amount: string;
  reversed_by: string | null;
  reversal_reason: string | null;
  ledger_hash: string | null;
  expense_account: string | null;
  account: string;
  account_name: string;
  source: string | null;
  supplier_needs_review: boolean;
  ready: boolean;
  hints: string[];
  blockers: string[];
  warnings: string[];
}

type Form = {
  supplier_id: string;
  file_id: string | null;
  file_name: string | null;
  receipt_date: string;
  service_date: string;
  invoice_number: string;
  description: string;
  category: string;
  tax_case: string;
  net_amount: string;
  tax_amount: string;
  expense_account: string;
  remember_rule: boolean;
};

const EMPTY: Form = {
  supplier_id: "",
  file_id: null,
  file_name: null,
  receipt_date: todayIso(),
  service_date: "",
  invoice_number: "",
  description: "",
  category: "other",
  tax_case: "IN_DE_19",
  net_amount: "",
  tax_amount: "",
  expense_account: "",
  remember_rule: false,
};

function de(v: string): string {
  return v.replace(".", ",");
}

function suggestTax(net: string, taxCase: string, meta: Meta | null): string {
  const tc = meta?.tax_cases.find((t) => t.code === taxCase);
  if (!tc || tc.reverse_charge || taxCase === "IN_OHNE_VST" || taxCase === "IN_NICHT_ABZIEHBAR") return "0,00";
  const n = Number(toApiDecimal(net));
  if (!Number.isFinite(n) || n === 0) return "";
  // nur Vorschlag (Anzeige) – maßgeblich ist der Betrag auf dem Beleg; Backend rechnet exakt mit Decimal
  const cents = Math.round(n * Number(tc.rate));
  return de((cents / 100).toFixed(2));
}

export default function ReceiptsPage() {
  const [tab, setTab] = useState<"receipts" | "suppliers">("receipts");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [form, setForm] = useState<(Form & { id: string | null }) | null>(null);
  const [supplierForm, setSupplierForm] = useState<(Omit<Supplier, "id"> & { id: string | null }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { toast, confirm, prompt } = useFeedback();
  const setNotice = (m: string | null) => { if (m) toast(m); };
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hints, setHints] = useState<string[]>([]);
  const [inbox, setInbox] = useState<{ id: string; file_name: string; mime_type: string; uploaded_by: string; uploaded_at: string }[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    void api<Meta>("GET", "/api/expense-meta").then(setMeta);
    void api<Supplier[]>("GET", "/api/suppliers").then(setSuppliers);
    void api<Receipt[]>("GET", "/api/receipts").then((r) => { setReceipts(r); setLoaded(true); });
    void api<typeof inbox>("GET", "/api/receipt-inbox/unassigned").then(setInbox);
    void api<Account[]>("GET", "/api/receipt-accounts").then(setAccounts);
  }, []);
  useEffect(load, [load]);

  const catLabel = useMemo(() => Object.fromEntries((meta?.categories ?? []).map((c) => [c.code, c.label])), [meta]);
  const taxLabel = useMemo(() => Object.fromEntries((meta?.tax_cases ?? []).map((c) => [c.code, c.label])), [meta]);
  const accLabel = useMemo(() => Object.fromEntries(accounts.map((a) => [a.number, `${a.number} ${a.name}`])), [accounts]);
  const ready = useMemo(() => receipts.filter((r) => r.status === "draft" && r.ready), [receipts]);
  const review = useMemo(() => receipts.filter((r) => r.status === "draft" && !r.ready), [receipts]);
  const booked = useMemo(() => receipts.filter((r) => r.status === "booked"), [receipts]);
  // Standardmäßig sind alle bereiten Belege ausgewählt
  useEffect(() => { setPicked(new Set(ready.map((r) => r.id))); }, [ready]);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof HttpError ? e.message : e instanceof Error ? e.message : "Aktion fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    await act(async () => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/receipt-files", { method: "POST", body: fd });
      const body = (await res.json().catch(() => ({}))) as { id?: string; detail?: string; duplicate_of_receipt?: string | null };
      if (!res.ok || !body.id) throw new Error(body.detail ?? `Fehler ${res.status}`);
      if (body.duplicate_of_receipt) setNotice("Diese Datei wurde bereits für einen anderen Beleg hochgeladen – bitte Dublette prüfen.");
      setForm((f) => (f ? { ...f, file_id: body.id ?? null, file_name: file.name } : f));
    });
  }

  interface Suggestion {
    source: "einvoice" | "ai"; supplier_id: string | null; supplier_name: string | null; supplier_vat_id: string | null; supplier_country: string;
    invoice_number: string | null; receipt_date: string | null; service_date: string | null; description: string; category: string; tax_case: string;
    net_amount: string | null; tax_amount: string; warnings: string[]; confidence: number | null; cached: boolean;
    expense_account: string | null; supplier_known: boolean;
  }

  async function recognize() {
    if (!form?.file_id) return;
    const fileId = form.file_id;
    await act(async () => {
      const sug = await api<Suggestion>("POST", `/api/receipt-files/${fileId}/extract`);
      let supplierId = sug.supplier_id;
      if (!supplierId && sug.supplier_name) {
        const created = await api<Supplier>("POST", "/api/suppliers", {
          name: sug.supplier_name, country_code: sug.supplier_country, vat_id: sug.supplier_vat_id,
          default_tax_case: sug.tax_case, default_category: sug.category,
        }).catch(() => null);
        if (created) { supplierId = created.id; setSuppliers((list) => [...list, created]); }
      }
      setForm((f) => f ? {
        ...f, supplier_id: supplierId ?? f.supplier_id, receipt_date: sug.receipt_date ?? f.receipt_date, service_date: sug.service_date ?? "",
        invoice_number: sug.invoice_number ?? "", description: sug.description, category: sug.category, tax_case: sug.tax_case,
        net_amount: sug.net_amount ? de(sug.net_amount) : f.net_amount, tax_amount: de(sug.tax_amount),
        expense_account: sug.expense_account ?? "", remember_rule: !sug.supplier_known,
      } : f);
      setHints([sug.source === "einvoice" ? "Aus E-Rechnung (XML) übernommen." : `Per KI erkannt${sug.confidence != null ? ` (Sicherheit ${Math.round(sug.confidence * 100)} %)` : ""} – bitte Beträge und Datum prüfen.`,
        sug.supplier_known ? "Konto, Kategorie und Steuerfall aus der Regel des Lieferanten übernommen." : "Neuer Lieferant: Konto wählen – es wird als Regel für künftige Belege gemerkt.",
        ...(!sug.supplier_id && supplierId ? [`Lieferant „${sug.supplier_name}“ neu angelegt.`] : []), ...sug.warnings]);
    });
  }

  function pickSupplier(id: string) {
    const s = suppliers.find((x) => x.id === id);
    const hasRule = !!s?.default_account && !s.needs_review;
    setForm((f) => (f ? { ...f, supplier_id: id, tax_case: s?.default_tax_case ?? f.tax_case, category: s?.default_category ?? f.category,
      expense_account: hasRule ? s?.default_account ?? "" : f.expense_account, remember_rule: !hasRule } : f));
  }

  const saveReceipt = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    void act(async () => {
      const body = {
        supplier_id: form.supplier_id,
        file_id: form.file_id,
        receipt_date: form.receipt_date,
        service_date: form.service_date || null,
        invoice_number: form.invoice_number,
        description: form.description,
        category: form.category,
        tax_case: form.tax_case,
        net_amount: toApiDecimal(form.net_amount),
        tax_amount: toApiDecimal(form.tax_amount || "0"),
        expense_account: form.expense_account || null,
        remember_rule: form.remember_rule,
      };
      const saved = form.id ? await api<Receipt>("PUT", `/api/receipts/${form.id}`, body) : await api<Receipt>("POST", "/api/receipts", body);
      setForm(null);
      setNotice(saved.warnings.length ? `Gespeichert. Hinweis: ${saved.warnings.join(" ")}`
        : form.remember_rule ? "Gespeichert und als Regel für diesen Lieferanten gemerkt – künftige Belege werden automatisch einsortiert."
        : "Beleg als Entwurf gespeichert.");
      load();
    });
  };

  const book = (r: Receipt) =>
    act(async () => {
      if (!(await confirm({ title: "Beleg verbindlich buchen?", confirmLabel: "Buchen",
        message: <>„{r.description}“ von {r.supplier_name} über <strong>{formatEuro(r.gross_amount)}</strong> wird festgeschrieben. Danach ist nur noch ein Storno möglich.</> }))) return;
      await api("POST", `/api/receipts/${r.id}/book`);
      setNotice("Beleg gebucht und festgeschrieben.");
      load();
    });

  const bookPicked = () =>
    act(async () => {
      const ids = ready.filter((r) => picked.has(r.id)).map((r) => r.id);
      if (!ids.length) return;
      const sum = ready.filter((r) => picked.has(r.id)).reduce((a, r) => a + Math.round(Number(r.gross_amount) * 100), 0) / 100;
      if (!(await confirm({ title: `${ids.length} ${ids.length === 1 ? "Beleg" : "Belege"} verbindlich buchen?`, confirmLabel: "Jetzt buchen",
        message: <>Summe <strong>{formatEuro(sum.toFixed(2))}</strong> brutto. Die Belege werden festgeschrieben – danach ist nur noch ein Storno möglich.</> }))) return;
      const res = await api<{ booked: string[]; skipped: { id: string; reason: string }[] }>("POST", "/api/receipts/book-ready", { receipt_ids: ids });
      setNotice(`${res.booked.length} Beleg(e) gebucht und festgeschrieben.${res.skipped.length ? ` ${res.skipped.length} übersprungen: ${res.skipped.map((x) => x.reason).join("; ")}` : ""}`);
      load();
    });

  const openForm = (r: Receipt) => {
    const s = suppliers.find((x) => x.id === r.supplier_id);
    setNotice(null);
    setHints([...r.blockers, ...r.hints]);
    setForm({ ...EMPTY, ...r, id: r.id, file_name: null, service_date: r.service_date ?? "", invoice_number: r.invoice_number ?? "",
      net_amount: de(r.net_amount), tax_amount: de(r.tax_amount), expense_account: r.expense_account ?? "",
      remember_rule: !!s?.needs_review || !s?.default_account });
  };

  const reverse = (r: Receipt) =>
    act(async () => {
      const reason = await prompt({ title: "Beleg stornieren", tone: "danger", confirmLabel: "Stornobuchung erstellen",
        message: <>Für „{r.description}“ ({formatEuro(r.gross_amount)}) wird eine Gegenbuchung erstellt; der Originalbeleg bleibt sichtbar.</>,
        input: { label: "Grund für das Storno", placeholder: "z. B. doppelt erfasst", minLength: 3 } });
      if (!reason) return;
      await api("POST", `/api/receipts/${r.id}/reverse`, { reason });
      setNotice("Stornobuchung erstellt.");
      load();
    });

  const remove = (r: Receipt) =>
    act(async () => {
      if (!(await confirm({ title: "Entwurf löschen?", tone: "danger", confirmLabel: "Löschen", message: `„${r.description}“ ist noch nicht gebucht und wird entfernt.` }))) return;
      await api("DELETE", `/api/receipts/${r.id}`);
      load();
    });

  const saveSupplier = (e: FormEvent) => {
    e.preventDefault();
    if (!supplierForm) return;
    void act(async () => {
      const f = supplierForm;
      // nur die erlaubten Felder senden (das Backend lehnt unbekannte Felder ab)
      const body = { name: f.name, country_code: f.country_code, vat_id: f.vat_id || null, default_tax_case: f.default_tax_case,
        default_category: f.default_category, default_account: f.default_account || null, notes: f.notes || null,
        iban: f.iban?.trim() || null, bic: f.bic?.trim() || null };
      if (f.id) await api("PUT", `/api/suppliers/${f.id}`, body);
      else await api("POST", "/api/suppliers", body);
      setNotice(f.needs_review ? `„${f.name}“ einsortiert – offene Belege übernehmen die Regel.` : "Lieferant gespeichert.");
      setSupplierForm(null);
      load();
    });
  };

  const selectedTax = meta?.tax_cases.find((t) => t.code === form?.tax_case);
  const totals = useMemo(() => {
    const booked = receipts.filter((r) => r.status === "booked");
    const sum = (k: keyof Receipt) => booked.reduce((acc, r) => acc + Math.round(Number(r[k]) * 100), 0) / 100;
    return { expense: sum("expense_amount").toFixed(2), input: sum("input_tax_amount").toFixed(2), rc: sum("rc_tax_amount").toFixed(2) };
  }, [receipts]);

  if (!loaded) return <PageSkeleton stats={3} />;

  return (
    <>
      <PageHeader
        title="Belege & Ausgaben"
        description="Ohne Beleg keine Buchung (GoBD). Gebuchte Belege werden festgeschrieben; Korrektur nur per Storno."
        actions={
          tab === "receipts" ? (
            <Button variant="primary" onClick={() => { setNotice(null); setHints([]); setForm({ ...EMPTY, id: null }); }}>+ Beleg</Button>
          ) : (
            <Button variant="primary" onClick={() => setSupplierForm({ id: null, name: "", country_code: "DE", vat_id: "", default_tax_case: "IN_DE_19", default_category: "other", default_account: null, needs_review: false, notes: "" })}>+ Lieferant</Button>
          )
        }
      />
      <Tabs value={tab} onChange={setTab} items={[{ value: "receipts", label: "Belege" }, { value: "suppliers", label: "Lieferanten" }]} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {tab === "receipts" && (
        <>
          {inbox.length > 0 && !form && (
            <Card className="mb-6">
              <h2 className="mb-1 font-semibold">Belegdateien ohne Beleg <span className="ml-1 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-950 dark:text-brand-200">{inbox.length}</span></h2>
              <p className="mb-3 text-sm text-slate-500">Aus dem Belegpostfach oder Uploads – erfassen, die Erkennung füllt die Felder vor.</p>
              <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
                {inbox.map((f) => (
                  <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span className="min-w-0 truncate">{f.file_name} <span className="text-xs text-slate-500">· {f.uploaded_by.startsWith("system:") ? "Belegpostfach" : f.uploaded_by} · {new Date(f.uploaded_at).toLocaleDateString("de-DE")}</span></span>
                    <span className="flex gap-2">
                      <a className="text-sm text-brand-700 hover:underline dark:text-brand-300" href={`/api/receipt-files/${f.id}`} target="_blank" rel="noopener">Ansehen</a>
                      <Button className="!px-2.5 !h-7 !py-0 !text-xs" variant="primary" onClick={() => { setNotice(null); setHints([]); setForm({ ...EMPTY, id: null, file_id: f.id, file_name: f.file_name }); }}>Erfassen</Button>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Drawer open={!!form} onClose={() => setForm(null)} size="xl"
            title={form?.id ? "Beleg prüfen und bearbeiten" : "Neuer Beleg"}
            subtitle="Links der Beleg, rechts die Daten – Beträge und Datum vor dem Speichern abgleichen."
            footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="receipt-form" variant="primary" disabled={busy}>Als Entwurf speichern</Button></>}>
          {form && (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
              <div className="lg:sticky lg:top-0 lg:self-start"><ReceiptPreview fileId={form.file_id} /></div>
              <form id="receipt-form" onSubmit={saveReceipt} className="grid content-start gap-4 sm:grid-cols-2">
                <Field label="Belegdatei (PDF, Bild oder E-Rechnung)" className="sm:col-span-2">
                  <div className="flex flex-wrap items-center gap-3">
                    <label className={`${linkButtonClass} cursor-pointer`}>
                      Datei wählen
                      <input type="file" aria-label="Belegdatei" accept="application/pdf,image/png,image/jpeg,.xml" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
                    </label>
                    <span className="min-w-0 truncate text-sm text-slate-500">{form.file_name ?? (form.file_id ? "Datei vorhanden" : "noch keine Datei – zum Buchen erforderlich")}</span>
                    {form.file_id && !form.id && <Button onClick={recognize} disabled={busy}>Automatisch erkennen</Button>}
                  </div>
                  {hints.length > 0 && <ul className="mt-2 space-y-0.5 text-xs text-amber-700 dark:text-amber-300">{hints.map((h) => <li key={h}>{h}</li>)}</ul>}
                </Field>
                <Field label="Lieferant">
                  <Select required value={form.supplier_id} onChange={(e) => pickSupplier(e.target.value)}>
                    <option value="">– auswählen –</option>
                    {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </Select>
                </Field>
                <Field label="Belegdatum">
                  <Input type="date" required value={form.receipt_date} onChange={(e) => setForm({ ...form, receipt_date: e.target.value })} />
                </Field>
                <Field label="Rechnungsnummer des Lieferanten">
                  <Input value={form.invoice_number} onChange={(e) => setForm({ ...form, invoice_number: e.target.value })} />
                </Field>
                <Field label="Beschreibung" className="sm:col-span-2">
                  <Input required value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
                </Field>
                <Field label="Kategorie">
                  <Select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                    {meta?.categories.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
                  </Select>
                </Field>
                <Field label="Konto (Aufwand / Anlage)" className="sm:col-span-2" hint={form.expense_account ? undefined : "leer = Standardkonto der Kategorie"}>
                  <Select value={form.expense_account} onChange={(e) => setForm({ ...form, expense_account: e.target.value })}>
                    <option value="">– nach Kategorie –</option>
                    {accounts.map((a) => <option key={a.number} value={a.number}>{a.number} {a.name}</option>)}
                  </Select>
                </Field>
                <label className="flex items-start gap-2.5 text-sm sm:col-span-2">
                  <input type="checkbox" className="mt-0.5" checked={form.remember_rule} onChange={(e) => setForm({ ...form, remember_rule: e.target.checked })} />
                  <span>Für diesen Lieferanten merken<span className="block text-xs text-slate-500">Konto, Kategorie und Steuerfall gelten dann für alle künftigen Belege.</span></span>
                </label>
                <Field label="Steuerfall" className="sm:col-span-2" hint={selectedTax?.reverse_charge ? "Reverse Charge: Auf dem Beleg steht keine USt. Sie schulden 19 % selbst und ziehen sie im selben Zug als Vorsteuer ab." : undefined}>
                  <Select value={form.tax_case} onChange={(e) => setForm({ ...form, tax_case: e.target.value, tax_amount: suggestTax(form.net_amount, e.target.value, meta) })}>
                    {meta?.tax_cases.map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
                  </Select>
                </Field>
                <Field label="Nettobetrag (€)">
                  <Input required inputMode="decimal" value={form.net_amount} onChange={(e) => setForm({ ...form, net_amount: e.target.value, tax_amount: suggestTax(e.target.value, form.tax_case, meta) })} />
                </Field>
                <Field label="USt laut Beleg (€)" hint="Betrag vom Beleg übernehmen">
                  <Input inputMode="decimal" value={form.tax_amount} disabled={!!selectedTax?.reverse_charge} onChange={(e) => setForm({ ...form, tax_amount: e.target.value })} />
                </Field>
                <Field label="Leistungsdatum (optional)">
                  <Input type="date" value={form.service_date} onChange={(e) => setForm({ ...form, service_date: e.target.value })} />
                </Field>
              </form>
            </div>
          )}
          </Drawer>

          <div className="mb-4 grid gap-3 sm:grid-cols-3">
            <Card><p className="text-xs text-slate-500">Aufwand (gebucht)</p><p className="text-xl font-semibold tabular">{formatEuro(totals.expense)}</p></Card>
            <Card><p className="text-xs text-slate-500">Vorsteuer (gebucht)</p><p className="text-xl font-semibold tabular">{formatEuro(totals.input)}</p></Card>
            <Card><p className="text-xs text-slate-500">davon Reverse Charge (USt selbst geschuldet)</p><p className="text-xl font-semibold tabular">{formatEuro(totals.rc)}</p></Card>
          </div>

          {receipts.length === 0 && <Card><Empty>Noch keine Belege. Rechnungen per Mail ans Belegpostfach schicken oder oben „+ Beleg“.</Empty></Card>}

          {ready.length > 0 && (
            <Card className="mb-6">
              <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="flex items-center gap-2 font-semibold">Bereit zum Buchen <Badge tone="green">{ready.length}</Badge></h2>
                  <p className="mt-0.5 text-[13px] text-slate-500">Erkannt und nach der Regel des Lieferanten kontiert – bitte kurz prüfen und buchen.</p>
                </div>
                <Button variant="primary" onClick={bookPicked} disabled={busy || picked.size === 0}>{picked.size} {picked.size === 1 ? "Beleg" : "Belege"} buchen</Button>
              </div>
              <Table head={<tr><th><input type="checkbox" aria-label="Alle auswählen" checked={picked.size === ready.length} onChange={(e) => setPicked(e.target.checked ? new Set(ready.map((r) => r.id)) : new Set())} /></th><th>Datum</th><th>Lieferant / Beschreibung</th><th>Konto</th><th className="text-right">Brutto</th><th /></tr>}>
                {ready.map((r) => (
                  <tr key={r.id}>
                    <td><input type="checkbox" aria-label={`${r.description} auswählen`} checked={picked.has(r.id)} onChange={(e) => { const n = new Set(picked); if (e.target.checked) n.add(r.id); else n.delete(r.id); setPicked(n); }} /></td>
                    <td className="tabular">{formatDate(r.receipt_date)}</td>
                    <td>{r.supplier_name}<div className="text-xs text-slate-500">{r.description}{r.invoice_number ? ` · ${r.invoice_number}` : ""} · {taxLabel[r.tax_case] ?? r.tax_case}</div></td>
                    <td className="text-xs"><span className="tabular font-medium">{r.account}</span> {r.account_name}</td>
                    <td className="text-right tabular">{formatEuro(r.gross_amount)}</td>
                    <td className="whitespace-nowrap text-right">
                      {r.file_id && <a className="mr-2 text-sm text-brand-700 hover:underline dark:text-brand-300" href={`/api/receipt-files/${r.file_id}`} target="_blank" rel="noopener">Datei</a>}
                      <Button className="!h-7 !px-2.5 !text-xs" onClick={() => openForm(r)}>Bearbeiten</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}

          {review.length > 0 && (
            <Card className="mb-6">
              <h2 className="flex items-center gap-2 font-semibold">Bitte prüfen <Badge tone="amber">{review.length}</Badge></h2>
              <p className="mb-3 mt-0.5 text-[13px] text-slate-500">Neue Lieferanten einmal einsortieren – danach laufen ihre Belege automatisch. Hinweise vor dem Buchen klären.</p>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {review.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm"><span className="font-medium">{r.supplier_name}</span> · {r.description} <span className="tabular text-slate-500">· {formatDate(r.receipt_date)} · {formatEuro(r.gross_amount)}</span></p>
                      <ul className="mt-1 space-y-0.5 text-xs">
                        {r.blockers.map((h) => <li key={h} className="text-amber-700 dark:text-amber-300">● {h}</li>)}
                        {r.hints.map((h) => <li key={h} className="text-slate-600 dark:text-slate-300">● {h}</li>)}
                      </ul>
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-2">
                      {r.file_id && <a className="self-center text-sm text-brand-700 hover:underline dark:text-brand-300" href={`/api/receipt-files/${r.file_id}`} target="_blank" rel="noopener">Datei</a>}
                      <Button variant={r.supplier_needs_review ? "primary" : "secondary"} onClick={() => openForm(r)}>{r.supplier_needs_review ? "Einsortieren" : "Bearbeiten"}</Button>
                      {!r.supplier_needs_review && r.blockers.length === 0 && <Button onClick={() => book(r)} disabled={busy} title="Nach Prüfung des Hinweises einzeln buchen">Trotzdem buchen</Button>}
                      <Button variant="ghost" onClick={() => remove(r)} disabled={busy}>Löschen</Button>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {booked.length > 0 && (
            <Card>
              <h2 className="mb-3 font-semibold">Gebucht</h2>
              <Table head={<tr><th>Datum</th><th>Lieferant</th><th>Beschreibung</th><th>Konto</th><th className="text-right">Brutto</th><th className="text-right">Vorsteuer</th><th>Status</th><th /></tr>}>
                {booked.map((r) => (
                  <tr key={r.id}>
                    <td className="tabular">{formatDate(r.receipt_date)}</td>
                    <td>{r.supplier_name}</td>
                    <td>{r.description}<div className="text-xs text-slate-500">{taxLabel[r.tax_case] ?? r.tax_case}{r.invoice_number ? ` · ${r.invoice_number}` : ""}</div></td>
                    <td className="text-xs"><span className="tabular font-medium">{r.account}</span> {r.account_name}</td>
                    <td className="text-right tabular">{formatEuro(r.gross_amount)}</td>
                    <td className="text-right tabular">{formatEuro(r.input_tax_amount)}</td>
                    <td>{r.kind === "reversal" ? <Badge tone="red">Storno</Badge> : r.reversed_by ? <Badge tone="amber">Storniert</Badge> : <Badge tone="green">Gebucht</Badge>}</td>
                    <td className="whitespace-nowrap text-right">
                      {r.file_id && <a className="mr-2 text-sm text-brand-700 hover:underline dark:text-brand-300" href={`/api/receipt-files/${r.file_id}`} target="_blank" rel="noopener">Datei</a>}
                      {r.kind === "expense" && !r.reversed_by && <Button variant="ghost" className="!h-7 !text-xs text-red-600" onClick={() => reverse(r)} disabled={busy}>Stornieren</Button>}
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}
          <p className="mt-3 text-xs text-slate-500">Kontierung über Lieferanten-Regeln (Standardkonto). Kategorien und Konten mit dem Steuerberater abstimmen.</p>
        </>
      )}

      {tab === "suppliers" && (
        <>
          <Drawer open={!!supplierForm} onClose={() => setSupplierForm(null)}
            title={supplierForm?.id ? `Lieferant: ${supplierForm.name}` : "Neuer Lieferant"}
            subtitle={supplierForm?.needs_review ? "Neu aus dem Belegpostfach – Regel festlegen, dann laufen seine Belege automatisch." : "Regel für die automatische Kontierung seiner Belege"}
            footer={<><Button onClick={() => setSupplierForm(null)}>Abbrechen</Button><Button type="submit" form="supplier-form" variant="primary" disabled={busy}>Speichern</Button></>}>
          {supplierForm && (
              <form id="supplier-form" onSubmit={saveSupplier} className="grid gap-4 sm:grid-cols-2">
                <Field label="Name" className="sm:col-span-2"><Input required value={supplierForm.name} onChange={(e) => setSupplierForm({ ...supplierForm, name: e.target.value })} /></Field>
                <Field label="Land (ISO)"><Input required maxLength={2} value={supplierForm.country_code} onChange={(e) => setSupplierForm({ ...supplierForm, country_code: e.target.value.toUpperCase() })} /></Field>
                <Field label="USt-IdNr (optional)"><Input value={supplierForm.vat_id ?? ""} onChange={(e) => setSupplierForm({ ...supplierForm, vat_id: e.target.value })} /></Field>
                <Field label="Standard-Steuerfall">
                  <Select value={supplierForm.default_tax_case} onChange={(e) => setSupplierForm({ ...supplierForm, default_tax_case: e.target.value })}>
                    {meta?.tax_cases.map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
                  </Select>
                </Field>
                <Field label="Standard-Kategorie">
                  <Select value={supplierForm.default_category} onChange={(e) => setSupplierForm({ ...supplierForm, default_category: e.target.value })}>
                    {meta?.categories.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
                  </Select>
                </Field>
                <Field label="Standardkonto" className="sm:col-span-2" hint="Mit Standardkonto werden Belege dieses Lieferanten automatisch kontiert (Buchen weiterhin per Klick).">
                  <Select value={supplierForm.default_account ?? ""} onChange={(e) => setSupplierForm({ ...supplierForm, default_account: e.target.value || null })}>
                    <option value="">– keins (Konto nach Kategorie) –</option>
                    {accounts.map((a) => <option key={a.number} value={a.number}>{a.number} {a.name}</option>)}
                  </Select>
                </Field>
                <Field label="IBAN" hint="für SEPA-Überweisungen"><Input value={supplierForm.iban ?? ""} onChange={(e) => setSupplierForm({ ...supplierForm, iban: e.target.value })} /></Field>
                <Field label="BIC (optional)"><Input value={supplierForm.bic ?? ""} onChange={(e) => setSupplierForm({ ...supplierForm, bic: e.target.value.toUpperCase() })} /></Field>
                <Field label="Notizen" className="sm:col-span-2"><Textarea value={supplierForm.notes ?? ""} onChange={(e) => setSupplierForm({ ...supplierForm, notes: e.target.value })} /></Field>
              </form>
          )}
          </Drawer>
          <Card>
            {suppliers.length === 0 ? <Empty>Noch keine Lieferanten.</Empty> : (
              <Table head={<tr><th>Name</th><th>Land</th><th>Regel</th><th /></tr>}>
                {[...suppliers].sort((x, y) => Number(y.needs_review) - Number(x.needs_review)).map((s) => (
                  <tr key={s.id}>
                    <td className="font-medium">{s.name}{s.needs_review && <span className="ml-2"><Badge tone="amber">neu – einsortieren</Badge></span>}</td>
                    <td>{s.country_code}{s.vat_id ? ` · ${s.vat_id}` : ""}</td>
                    <td className="text-xs">{s.default_account ? <span className="font-medium">{accLabel[s.default_account] ?? s.default_account}</span> : <span className="text-slate-500">kein Standardkonto</span>}<div className="text-slate-500">{taxLabel[s.default_tax_case]} · {catLabel[s.default_category]}</div></td>
                    <td className="text-right"><Button className="!h-7 !text-xs" onClick={() => setSupplierForm({ ...s })}>Bearbeiten</Button></td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        </>
      )}
    </>
  );
}
