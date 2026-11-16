"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input, Select, Textarea, inputClass } from "@/components/ui";
import { calculateInvoice, formatEuro, toApiDecimal, type CalculateResponse } from "@/lib/api";
import { convertPrice, rateOf } from "@/lib/decimal";
import { api, HttpError } from "@/lib/http";
import { TAX_CASES, UNITS, type Customer, type Invoice, type InvoiceLine, type Product } from "@/lib/types";

type Row = InvoiceLine & { key: number; recurring?: boolean };

export interface OfferDraft {
  id: string;
  customer_id: string;
  title: string;
  valid_until: string;
  intro_text: string | null;
  closing_text: string | null;
  lines: (InvoiceLine & { recurring: boolean })[];
}

function inDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
let keySeq = 1;

function toGerman(v: string): string {
  const trimmed = v.includes(".") ? v.replace(/0+$/, "").replace(/\.$/, "") : v;
  return trimmed.replace(".", ",");
}

function firstOfMonth(offset = 0): string {
  const d = new Date();
  const f = new Date(d.getFullYear(), d.getMonth() + offset, 1);
  return `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}-01`;
}

function lastOfMonth(offset = 0): string {
  const d = new Date();
  const l = new Date(d.getFullYear(), d.getMonth() + offset + 1, 0);
  return `${l.getFullYear()}-${String(l.getMonth() + 1).padStart(2, "0")}-${String(l.getDate()).padStart(2, "0")}`;
}

export function InvoiceEditor({ invoice, offer, kind = "invoice" }: { invoice?: Invoice; offer?: OfferDraft; kind?: "invoice" | "offer" }) {
  const router = useRouter();
  const isOffer = kind === "offer";
  const [title, setTitle] = useState(offer?.title ?? "");
  const [validUntil, setValidUntil] = useState(offer?.valid_until ?? inDays(30));
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [customerId, setCustomerId] = useState(invoice?.customer_id ?? offer?.customer_id ?? "");
  const [from, setFrom] = useState(invoice?.service_date_from ?? firstOfMonth());
  const [to, setTo] = useState(invoice?.service_date_to ?? lastOfMonth());
  const [dueDays, setDueDays] = useState<string>(invoice ? String(invoice.due_days) : "");
  const [intro, setIntro] = useState(invoice?.intro_text ?? offer?.intro_text ?? "");
  const [closing, setClosing] = useState(invoice?.closing_text ?? offer?.closing_text ?? "");
  const [rows, setRows] = useState<Row[]>(
    (invoice?.lines ?? offer?.lines)?.map((l) => ({ ...l, key: keySeq++, quantity: toGerman(l.quantity), unit_price: toGerman(l.unit_price) })) ?? [],
  );
  const [preview, setPreview] = useState<CalculateResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api<Customer[]>("GET", "/api/customers").then(setCustomers);
    void api<Product[]>("GET", "/api/products").then(setProducts);
  }, []);

  const customer = customers.find((c) => c.id === customerId);
  const mode: "net" | "gross" = customer?.customer_type === "b2c" ? "gross" : "net";

  const apiLines = useMemo(
    () =>
      rows.map(({ key: _k, position: _p, recurring, ...r }) => ({
        ...r,
        ...(isOffer ? { recurring: !!recurring } : {}),
        quantity: toApiDecimal(r.quantity),
        unit_price: toApiDecimal(r.unit_price),
      })),
    [rows, isOffer],
  );

  // Live-Vorschau mit derselben Berechnung wie bei der Freigabe (Backend)
  useEffect(() => {
    if (rows.length === 0 || rows.some((r) => !r.description.trim() || !r.unit_price || !r.quantity)) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    const t = setTimeout(() => {
      calculateInvoice(
        mode,
        apiLines.map(({ product_id: _pid, recurring: _rec, ...l }: Record<string, unknown>) => l as Omit<InvoiceLine, "product_id">),
      )
        .then((p) => {
          setPreview(p);
          setPreviewError(null);
        })
        .catch((e: unknown) => {
          setPreview(null);
          setPreviewError(e instanceof Error ? e.message : "Berechnung fehlgeschlagen");
        });
    }, 300);
    return () => clearTimeout(t);
  }, [apiLines, mode, rows]);

  function addProduct(id: string) {
    const p = products.find((x) => x.id === id);
    if (!p) return;
    const price = convertPrice(p.price, rateOf(p.tax_case), p.price_basis, mode);
    setRows((rs) => [
      ...rs,
      { key: keySeq++, product_id: p.id, description: p.name, quantity: "1", unit_code: p.unit_code, unit_price: toGerman(price), tax_case: p.tax_case, recurring: p.billing === "monthly" },
    ]);
  }

  const update = (key: number, patch: Partial<Row>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (isOffer) {
        const body = { customer_id: customerId, title, valid_until: validUntil, intro_text: intro, closing_text: closing, lines: apiLines };
        const saved = offer ? await api<OfferDraft>("PUT", `/api/offers/${offer.id}`, body) : await api<OfferDraft>("POST", "/api/offers", body);
        router.push(`/angebote/${saved.id}?gespeichert=1`);
        return;
      }
      const body = {
        customer_id: customerId,
        service_date_from: from || null,
        service_date_to: to || null,
        due_days: dueDays === "" ? null : Number(dueDays),
        intro_text: intro,
        closing_text: closing,
        lines: apiLines,
      };
      const saved = invoice
        ? await api<Invoice>("PUT", `/api/invoices/${invoice.id}`, body)
        : await api<Invoice>("POST", "/api/invoices", body);
      if (invoice) router.refresh();
      router.push(`/rechnungen/${saved.id}?gespeichert=1`);
    } catch (err) {
      setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-6">
      <Card>
        <div className="grid gap-4 md:grid-cols-4">
          <Field label="Kunde" className="md:col-span-2">
            <Select required value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
              <option value="">– auswählen –</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name} ({c.number}, {c.customer_type === "b2b" ? "Firma" : "Privat"})
                </option>
              ))}
            </Select>
          </Field>
          {isOffer ? (
            <>
              <Field label="Betreff">
                <Input required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <Field label="Gültig bis">
                <Input type="date" required value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
              </Field>
            </>
          ) : (<>
          <Field label="Leistung von" hint="Pflichtangabe">
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="Leistung bis">
            <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </Field>
          <Field label="Zahlungsziel (Tage)" hint={customer ? `Standard des Kunden: ${customer.payment_terms_days}` : undefined}>
            <Input type="number" min={0} max={120} value={dueDays} placeholder={customer ? String(customer.payment_terms_days) : ""} onChange={(e) => setDueDays(e.target.value)} />
          </Field>
          </>)}
          <div className="text-sm text-slate-500 md:col-span-3 md:self-end md:pb-2">
            {customer ? (mode === "gross" ? "Privatkunde: Preise werden brutto (inkl. USt) eingegeben." : "Firmenkunde: Preise werden netto eingegeben.") : ""}
          </div>
        </div>
      </Card>

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-medium">Positionen</h2>
          <div className="flex w-full flex-wrap gap-2 sm:w-auto">
            <select aria-label="Produkt hinzufügen" className={`${inputClass} min-w-0 flex-1 sm:w-64 sm:flex-none`} value="" onChange={(e) => addProduct(e.target.value)} disabled={!customer}>
              <option value="">+ Produkt hinzufügen …</option>
              {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <Button
              disabled={!customer}
              onClick={() => setRows((rs) => [...rs, { key: keySeq++, product_id: null, description: "", quantity: "1", unit_code: "HUR", unit_price: "", tax_case: "OUT_DE_19" }])}
            >
              + Freie Position
            </Button>
          </div>
        </div>
        {!customer && <p className="text-sm text-slate-500">Zuerst einen Kunden auswählen.</p>}
        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Bezeichnung</th>
                  <th className="w-24 pb-2 font-medium">Menge</th>
                  <th className="w-28 pb-2 font-medium">Einheit</th>
                  <th className="w-36 pb-2 font-medium">Preis {mode === "net" ? "netto" : "brutto"} (€)</th>
                  <th className="w-24 pb-2 font-medium">USt</th>
                  {isOffer && <th className="w-20 pb-2 font-medium" title="Monatlich wiederkehrend → wird bei Annahme zum Vertrag">Monatl.</th>}
                  <th className="w-28 pb-2 text-right font-medium">Betrag</th>
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody className="align-top">
                {rows.map((r, idx) => {
                  const pl = preview?.lines[idx];
                  return (
                    <tr key={r.key}>
                      <td className="py-1 pr-2"><Input aria-label="Bezeichnung" required value={r.description} onChange={(e) => update(r.key, { description: e.target.value })} /></td>
                      <td className="py-1 pr-2"><Input aria-label="Menge" className="tabular text-right" inputMode="decimal" required value={r.quantity} onChange={(e) => update(r.key, { quantity: e.target.value })} /></td>
                      <td className="py-1 pr-2">
                        <Select aria-label="Einheit" value={r.unit_code} onChange={(e) => update(r.key, { unit_code: e.target.value })}>
                          {UNITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </Select>
                      </td>
                      <td className="py-1 pr-2"><Input aria-label="Preis" className="tabular text-right" inputMode="decimal" required placeholder="0,00" value={r.unit_price} onChange={(e) => update(r.key, { unit_price: e.target.value })} /></td>
                      <td className="py-1 pr-2">
                        <Select aria-label="Steuersatz" value={r.tax_case} onChange={(e) => update(r.key, { tax_case: e.target.value })}>
                          {TAX_CASES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </Select>
                      </td>
                      {isOffer && (
                        <td className="py-2 pr-2 text-center">
                          <input type="checkbox" aria-label="Monatlich wiederkehrend" checked={!!r.recurring} onChange={(e) => update(r.key, { recurring: e.target.checked })} />
                        </td>
                      )}
                      <td className="py-2 text-right tabular">{pl ? formatEuro(mode === "gross" ? pl.line_gross : pl.line_net) : "–"}</td>
                      <td className="py-1">
                        <button type="button" aria-label="Position entfernen" onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))} className="rounded-md px-2 py-1.5 text-slate-400 hover:text-red-600">✕</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {previewError && <div className="mt-3"><Alert>{previewError}</Alert></div>}
        {preview && (
          <dl className="ml-auto mt-4 max-w-xs space-y-1 text-sm tabular">
            <div className="flex justify-between"><dt className="text-slate-500">Summe netto</dt><dd>{formatEuro(preview.total_net)}</dd></div>
            {preview.tax_breakdown.map((b) => (
              <div key={b.rate} className="flex justify-between"><dt className="text-slate-500">USt {b.rate.replace(/\.?0+$/, "")} %</dt><dd>{formatEuro(b.tax_amount)}</dd></div>
            ))}
            <div className="flex justify-between border-t border-slate-200 pt-1 font-semibold dark:border-slate-700"><dt>Gesamt</dt><dd>{formatEuro(preview.total_gross)}</dd></div>
            {preview.warnings.map((w) => <p key={w} className="pt-2 text-xs text-amber-700 dark:text-amber-300">{w}</p>)}
          </dl>
        )}
      </Card>

      <Card>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Einleitungstext (optional)"><Textarea value={intro} onChange={(e) => setIntro(e.target.value)} /></Field>
          <Field label="Schlusstext (optional)"><Textarea value={closing} onChange={(e) => setClosing(e.target.value)} /></Field>
        </div>
      </Card>

      {error && <Alert>{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={busy || !customer || rows.length === 0}>
          {busy ? "Speichere …" : "Entwurf speichern"}
        </Button>
        <Button onClick={() => router.push(isOffer ? (offer ? `/angebote/${offer.id}` : "/angebote") : invoice ? `/rechnungen/${invoice.id}` : "/rechnungen")}>Abbrechen</Button>
      </div>
    </form>
  );
}
