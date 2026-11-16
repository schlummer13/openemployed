"use client";

import { useState, type FormEvent } from "react";
import {
  ApiError,
  calculateInvoice,
  formatEuro,
  toApiDecimal,
  type CalculateResponse,
  type LineIn,
  type PriceMode,
} from "@/lib/api";

const UNITS: [string, string][] = [
  ["MON", "Monat"],
  ["HUR", "Stunde"],
  ["C62", "Stück"],
  ["LS", "Pauschale"],
  ["DAY", "Tag"],
];

const TAX_CASES: [string, string][] = [
  ["OUT_DE_19", "19 %"],
  ["OUT_DE_7", "7 %"],
  ["OUT_EU_B2B_SERVICE", "EU-Leistung (Reverse Charge)"],
  ["OUT_EU_B2B_GOODS", "ig. Lieferung (EU)"],
  ["OUT_DRITTLAND_SERVICE", "Drittland (nicht steuerbar)"],
];

type Row = LineIn & { key: number };

let nextKey = 1;
const emptyRow = (): Row => ({
  key: nextKey++,
  description: "",
  quantity: "1",
  unit_code: "MON",
  unit_price: "",
  tax_case: "OUT_DE_19",
});

const input =
  "w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100 dark:border-slate-700 dark:bg-slate-950";

export function InvoiceCalculator() {
  const [mode, setMode] = useState<PriceMode>("net");
  const [rows, setRows] = useState<Row[]>([
    { ...emptyRow(), description: "Software-Miete HR (monatlich)", unit_price: "1758,00" },
  ]);
  const [result, setResult] = useState<CalculateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const update = (key: number, patch: Partial<LineIn>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const lines = rows.map(({ key: _key, ...r }) => ({
        ...r,
        quantity: toApiDecimal(r.quantity),
        unit_price: toApiDecimal(r.unit_price),
      }));
      setResult(await calculateInvoice(mode, lines));
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError ? err.message : "Backend nicht erreichbar");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-medium">Rechnungsrechner</h2>
          <p className="text-sm text-slate-500">Berechnung nach EN 16931: Steuer je Steuersatz auf die Summe der Positionen.</p>
        </div>
        <div role="radiogroup" aria-label="Preisangabe" className="inline-flex rounded-lg border border-slate-300 p-0.5 text-sm dark:border-slate-700">
          {(["net", "gross"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1 ${mode === m ? "bg-brand-600 text-white" : "text-slate-600 dark:text-slate-300"}`}
            >
              {m === "net" ? "Netto (B2B)" : "Brutto (B2C)"}
            </button>
          ))}
        </div>
      </div>

      <form onSubmit={submit} className="space-y-3">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="pb-2 font-medium">Bezeichnung</th>
                <th className="w-24 pb-2 font-medium">Menge</th>
                <th className="w-32 pb-2 font-medium">Einheit</th>
                <th className="w-36 pb-2 font-medium">Preis {mode === "net" ? "netto" : "brutto"} (€)</th>
                <th className="w-24 pb-2 font-medium">USt</th>
                <th className="w-10" />
              </tr>
            </thead>
            <tbody className="align-top">
              {rows.map((r) => (
                <tr key={r.key}>
                  <td className="py-1 pr-2">
                    <input className={input} aria-label="Bezeichnung" required value={r.description} onChange={(e) => update(r.key, { description: e.target.value })} />
                  </td>
                  <td className="py-1 pr-2">
                    <input className={`${input} tabular text-right`} aria-label="Menge" inputMode="decimal" required value={r.quantity} onChange={(e) => update(r.key, { quantity: e.target.value })} />
                  </td>
                  <td className="py-1 pr-2">
                    <select className={input} aria-label="Einheit" value={r.unit_code} onChange={(e) => update(r.key, { unit_code: e.target.value })}>
                      {UNITS.map(([v, l]) => (
                        <option key={v} value={v}>{l}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-1 pr-2">
                    <input className={`${input} tabular text-right`} aria-label="Preis" inputMode="decimal" placeholder="0,00" required value={r.unit_price} onChange={(e) => update(r.key, { unit_price: e.target.value })} />
                  </td>
                  <td className="py-1 pr-2">
                    <select className={input} aria-label="Steuersatz" value={r.tax_case} onChange={(e) => update(r.key, { tax_case: e.target.value })}>
                      {TAX_CASES.map(([v, l]) => (
                        <option key={v} value={v}>{l}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-1">
                    <button
                      type="button"
                      aria-label="Position entfernen"
                      disabled={rows.length === 1}
                      onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}
                      className="rounded-md px-2 py-1.5 text-slate-400 hover:text-red-600 disabled:opacity-30"
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setRows((rs) => [...rs, emptyRow()])} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">
            + Position
          </button>
          <button type="submit" disabled={busy} className="rounded-md bg-brand-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60">
            {busy ? "Berechne …" : "Berechnen"}
          </button>
        </div>
      </form>

      {error && (
        <p role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          <div>
            <h3 className="mb-2 text-sm font-medium text-slate-500">Steueraufschlüsselung</h3>
<div className="overflow-x-auto">            <table className="w-full text-sm tabular">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-1 font-medium">Satz</th>
                  <th className="pb-1 text-right font-medium">Netto</th>
                  <th className="pb-1 text-right font-medium">USt</th>
                </tr>
              </thead>
              <tbody>
                {result.tax_breakdown.map((b) => (
                  <tr key={`${b.category}-${b.rate}`} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1">{b.rate.replace(/\.?0+$/, "")} %</td>
                    <td className="py-1 text-right">{formatEuro(b.taxable_amount)}</td>
                    <td className="py-1 text-right">{formatEuro(b.tax_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </div>
          <dl className="space-y-1 text-sm tabular">
            <div className="flex justify-between"><dt className="text-slate-500">Summe netto</dt><dd>{formatEuro(result.total_net)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">Umsatzsteuer</dt><dd>{formatEuro(result.total_tax)}</dd></div>
            <div className="flex justify-between border-t border-slate-200 pt-1 text-base font-semibold dark:border-slate-700"><dt>Gesamtbetrag</dt><dd>{formatEuro(result.total_gross)}</dd></div>
            {result.warnings.map((w) => (
              <p key={w} className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{w}</p>
            ))}
          </dl>
        </div>
      )}
    </section>
  );
}
