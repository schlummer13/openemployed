"use client";

import { formatEuro } from "@/lib/api";
import { formatDate } from "@/lib/http";
import { unitLabel, type Invoice } from "@/lib/types";

// Struktur der festgeschriebenen Fassung (siehe backend/app/services/invoices.py build_snapshot)
interface Snapshot {
  kind: "invoice" | "cancellation";
  issue_date: string;
  due_date: string;
  service_period: { from: string | null; to: string | null };
  price_mode: "net" | "gross";
  seller: {
    legal_name: string; street: string; postal_code: string; city: string; register_court: string | null;
    register_number: string | null; managing_directors: string[]; tax_number: string | null; vat_id: string | null;
    iban: string | null; bic: string | null; bank_name: string | null; email: string | null; phone: string | null; website: string | null;
  };
  buyer: { number: string; name: string; street: string; postal_code: string; city: string; vat_id: string | null; buyer_reference: string | null };
  lines: { position: number; description: string; quantity: string; unit_code: string; unit_price_net: string; unit_price_gross: string | null; line_net: string; line_gross: string | null; rate: string }[];
  tax_breakdown: { rate: string; taxable_amount: string; tax_amount: string }[];
  totals: { net: string; tax: string; gross: string; gross_target: string | null; gross_deviation: string };
  notes: string[];
  intro_text: string | null;
  closing_text: string | null;
  cancellation_reason: string | null;
}

function qty(v: string): string {
  return v.replace(/\.?0+$/, "").replace("-", "−").replace(".", ",");
}

/** Bildschirmansicht der festgeschriebenen Rechnung (das PDF folgt in Phase 1b). */
export function InvoiceView({ invoice }: { invoice: Invoice }) {
  const s = invoice.snapshot as unknown as Snapshot;
  const gross = s.price_mode === "gross";
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-8 text-sm shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap justify-between gap-6">
        <div>
          <p className="text-xs text-slate-500">{s.seller.legal_name} · {s.seller.street} · {s.seller.postal_code} {s.seller.city}</p>
          <div className="mt-3 leading-relaxed">
            <p className="font-medium">{s.buyer.name}</p>
            <p>{s.buyer.street}</p>
            <p>{s.buyer.postal_code} {s.buyer.city}</p>
          </div>
        </div>
        <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5 text-right tabular">
          <dt className="text-slate-500">{s.kind === "cancellation" ? "Stornorechnung" : "Rechnung"} Nr.</dt><dd className="font-semibold">{invoice.number}</dd>
          <dt className="text-slate-500">Datum</dt><dd>{formatDate(s.issue_date)}</dd>
          <dt className="text-slate-500">Leistung</dt><dd>{formatDate(s.service_period.from)}{s.service_period.to ? ` – ${formatDate(s.service_period.to)}` : ""}</dd>
          <dt className="text-slate-500">Kunden-Nr.</dt><dd>{s.buyer.number}</dd>
          {s.buyer.buyer_reference && (<><dt className="text-slate-500">Ihre Referenz</dt><dd>{s.buyer.buyer_reference}</dd></>)}
          {s.kind !== "cancellation" && (<><dt className="text-slate-500">Fällig am</dt><dd>{formatDate(s.due_date)}</dd></>)}
        </dl>
      </div>

      {s.intro_text && <p className="mt-6 whitespace-pre-line">{s.intro_text}</p>}

      <div className="overflow-x-auto"><table className="mt-6 w-full tabular">
        <thead className="border-b border-slate-300 text-left text-xs text-slate-500 dark:border-slate-700">
          <tr>
            <th className="pb-1 font-medium">Pos.</th>
            <th className="pb-1 font-medium">Bezeichnung</th>
            <th className="pb-1 text-right font-medium">Menge</th>
            <th className="pb-1 text-right font-medium">Einzelpreis {gross ? "brutto" : "netto"}</th>
            <th className="pb-1 text-right font-medium">USt</th>
            <th className="pb-1 text-right font-medium">Betrag {gross ? "brutto" : "netto"}</th>
          </tr>
        </thead>
        <tbody>
          {s.lines.map((l) => (
            <tr key={l.position} className="border-b border-slate-100 dark:border-slate-800">
              <td className="py-1.5">{l.position}</td>
              <td className="py-1.5">{l.description}</td>
              <td className="py-1.5 text-right">{qty(l.quantity)} {unitLabel(l.unit_code)}</td>
              <td className="py-1.5 text-right">{formatEuro(gross ? l.unit_price_gross : l.unit_price_net)}</td>
              <td className="py-1.5 text-right">{l.rate.replace(/\.?0+$/, "")} %</td>
              <td className="py-1.5 text-right">{formatEuro(gross ? l.line_gross : l.line_net)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>

      <dl className="ml-auto mt-4 max-w-xs space-y-1 tabular">
        <div className="flex justify-between"><dt className="text-slate-500">Summe netto</dt><dd>{formatEuro(s.totals.net)}</dd></div>
        {s.tax_breakdown.map((b) => (
          <div key={b.rate} className="flex justify-between">
            <dt className="text-slate-500">USt {b.rate.replace(/\.?0+$/, "")} % auf {formatEuro(b.taxable_amount)}</dt><dd>{formatEuro(b.tax_amount)}</dd>
          </div>
        ))}
        {s.totals.gross_target && Number(s.totals.gross_deviation) !== 0 && (
          <>
            <div className="flex justify-between"><dt className="text-slate-500">Summe Positionen brutto</dt><dd>{formatEuro(s.totals.gross_target)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">Rundungsausgleich zu Ihren Gunsten</dt><dd>{formatEuro(s.totals.gross_deviation.startsWith("-") ? s.totals.gross_deviation.slice(1) : `-${s.totals.gross_deviation}`)}</dd></div>
          </>
        )}
        <div className="flex justify-between border-t border-slate-300 pt-1 text-base font-semibold dark:border-slate-700"><dt>Gesamtbetrag</dt><dd>{formatEuro(s.totals.gross)}</dd></div>
      </dl>

      {s.cancellation_reason && <p className="mt-6">Grund: {s.cancellation_reason}</p>}
      {s.notes.map((n) => <p key={n} className="mt-2 text-slate-600 dark:text-slate-400">{n}</p>)}
      {s.closing_text && <p className="mt-4 whitespace-pre-line">{s.closing_text}</p>}
      {s.kind !== "cancellation" && s.seller.iban && (
        <p className="mt-4">Bitte überweisen Sie den Betrag bis zum {formatDate(s.due_date)} unter Angabe der Rechnungsnummer {invoice.number} auf das unten genannte Konto.</p>
      )}

      <footer className="mt-8 grid gap-4 border-t border-slate-200 pt-4 text-xs text-slate-500 md:grid-cols-3 dark:border-slate-800">
        <div>
          <p>{s.seller.legal_name}</p>
          <p>{s.seller.street}, {s.seller.postal_code} {s.seller.city}</p>
          <p>Geschäftsführer: {s.seller.managing_directors.join(", ")}</p>
        </div>
        <div>
          {s.seller.register_court && <p>{s.seller.register_court}, {s.seller.register_number}</p>}
          {s.seller.tax_number && <p>Steuernummer: {s.seller.tax_number}</p>}
          {s.seller.vat_id && <p>USt-IdNr: {s.seller.vat_id}</p>}
        </div>
        <div>
          {s.seller.bank_name && <p>{s.seller.bank_name}</p>}
          {s.seller.iban && <p>IBAN: {s.seller.iban.replace(/(.{4})/g, "$1 ").trim()}</p>}
          {s.seller.bic && <p>BIC: {s.seller.bic}</p>}
        </div>
      </footer>
    </article>
  );
}
