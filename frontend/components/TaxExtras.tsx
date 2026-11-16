"use client";

import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "./Feedback";
import { Badge, Button, Card, CardHeader, Empty, Field, Input, Select } from "./ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

interface Prepay { id: string; tax: "kst" | "gewst" | "ust_svz"; due_date: string; amount: string; soli: string; paid_on: string | null }
interface PrepayData { items: Prepay[]; vat_extension: boolean; vat_filing: string; svz_proposal: { basis: string; amount: string } | null }
const TAX: Record<string, string> = { kst: "Körperschaftsteuer", gewst: "Gewerbesteuer", ust_svz: "USt-Sondervorauszahlung" };
const isoDate = (de: string) => { const m = de.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/); return m ? `${m[3]}-${m[2]!.padStart(2, "0")}-${m[1]!.padStart(2, "0")}` : null; };

/** Vorauszahlungen laut Bescheid (KSt/GewSt) und Dauerfristverlängerung mit Sondervorauszahlung. */
export function Prepayments({ year }: { year: number }) {
  const [data, setData] = useState<PrepayData | null>(null);
  const [form, setForm] = useState({ tax: "kst", quarterly: "", soli: "" });
  const { toast, prompt, confirm } = useFeedback();
  const load = useCallback(() => { void api<PrepayData>("GET", `/api/reports/prepayments/${year}`).then(setData); }, [year]);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); toast(msg); load(); } catch (e) { toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"); } };
  if (!data) return <Card><p className="text-sm text-slate-500">Lade …</p></Card>;
  const open = data.items.filter((i) => !i.paid_on).reduce((s, i) => s + Number(i.amount) + Number(i.soli), 0);
  return (
    <Card>
      <CardHeader title={`Vorauszahlungen ${year}`} description={`Laut Vorauszahlungsbescheid – erscheinen im Steuerkalender und in der Liquiditätsvorschau. Offen: ${formatEuro(open)}`} />
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <Field label="Steuer" className="w-40"><Select value={form.tax} onChange={(e) => setForm({ ...form, tax: e.target.value })}><option value="kst">Körperschaftsteuer</option><option value="gewst">Gewerbesteuer</option></Select></Field>
        <Field label="je Quartal (€)" className="w-32"><Input inputMode="decimal" value={form.quarterly} onChange={(e) => setForm({ ...form, quarterly: e.target.value })} /></Field>
        {form.tax === "kst" && <Field label="Soli je Quartal" className="w-28"><Input inputMode="decimal" value={form.soli} onChange={(e) => setForm({ ...form, soli: e.target.value })} /></Field>}
        <Button onClick={() => void act(() => api("PUT", "/api/reports/prepayments/schedule", { tax: form.tax, year, quarterly: toApiDecimal(form.quarterly || "0"), soli: toApiDecimal(form.soli || "0") }), "Termine übernommen.")}>Übernehmen</Button>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-white/[0.03]">
        <label className="flex items-center gap-2"><input type="checkbox" checked={data.vat_extension} onChange={(e) => void act(() => api("PUT", "/api/reports/vat-extension", { enabled: e.target.checked }), e.target.checked ? "Dauerfristverlängerung aktiv." : "Dauerfristverlängerung aus.")} />Dauerfristverlängerung (USt einen Monat später)</label>
        {data.svz_proposal && (
          <Button className="!h-7 !text-xs" onClick={async () => {
            if (await confirm({ title: `Sondervorauszahlung ${year}: ${formatEuro(data.svz_proposal!.amount)}`, confirmLabel: "Übernehmen", message: `1/11 der Vorauszahlungen ${year - 1} (${formatEuro(data.svz_proposal!.basis)}). Fällig zum 10.02.; wird in der Dezember-Voranmeldung (Kz 39) angerechnet.` }))
              void act(() => api("PUT", "/api/reports/prepayments/svz", { year, amount: data.svz_proposal!.amount }), "Sondervorauszahlung erfasst.");
          }}>Sondervorauszahlung {formatEuro(data.svz_proposal.amount)} übernehmen</Button>
        )}
      </div>
      {data.items.length === 0 ? <Empty>Keine Vorauszahlungen festgesetzt.</Empty> : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-white/5">
          {data.items.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-3 py-2">
              <span className="w-24 tabular">{formatDate(i.due_date)}</span>
              <span className="min-w-0 flex-1">{TAX[i.tax]}</span>
              <span className="tabular">{formatEuro(Number(i.amount) + Number(i.soli))}</span>
              {i.paid_on ? <Badge tone="green">bezahlt {formatDate(i.paid_on)}</Badge> : (
                <Button className="!h-7 !text-xs" onClick={async () => {
                  const d = await prompt({ title: `${TAX[i.tax]} als bezahlt buchen`, confirmLabel: "Buchen", input: { label: "Zahlungsdatum (TT.MM.JJJJ)" } });
                  if (!d) return;
                  const iso = isoDate(d);
                  if (!iso) { toast("Datum als TT.MM.JJJJ eingeben.", "error"); return; }
                  void act(() => api("POST", `/api/reports/prepayments/${i.id}/paid`, { paid_on: iso }), "Zahlung gebucht.");
                }}>Bezahlt</Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

interface ZmLine { country: string; vat_id: string; kind: "L" | "S"; amount: string; amount_euro: string; name: string | null; invoices: string[] }

/** Zusammenfassende Meldung (§ 18a UStG) – Upload als CSV im BZStOnline-Portal. */
export function ZmPanel({ year }: { year: number }) {
  const [spec, setSpec] = useState(`${year}-Q${Math.floor(new Date().getMonth() / 3) + 1}`);
  const [data, setData] = useState<{ lines: ZmLine[]; total: string; missing_vat_id: string[] } | null>(null);
  useEffect(() => { void api<{ lines: ZmLine[]; total: string; missing_vat_id: string[] }>("GET", `/api/reports/zm/${spec}`).then(setData).catch(() => setData(null)); }, [spec]);
  const specs = [1, 2, 3, 4].map((q) => `${year}-Q${q}`).concat(Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`));
  return (
    <Card>
      <CardHeader title="Zusammenfassende Meldung" description="Innergemeinschaftliche Lieferungen (L) und sonstige Leistungen an EU-Unternehmer (S) – bis zum 25. nach Ablauf des Zeitraums an das BZSt."
        actions={<Select aria-label="Meldezeitraum" value={spec} onChange={(e) => setSpec(e.target.value)} className="!w-32">{specs.map((s) => <option key={s}>{s}</option>)}</Select>} />
      {!data ? <p className="text-sm text-slate-500">Lade …</p> : data.lines.length === 0 ? <Empty>Keine meldepflichtigen EU-Umsätze in {spec}.</Empty> : (
        <>
          <ul className="divide-y divide-slate-100 text-sm dark:divide-white/5">
            {data.lines.map((l) => <li key={`${l.country}${l.vat_id}${l.kind}`} className="flex flex-wrap gap-3 py-2"><span className="w-36 tabular">{l.country} {l.vat_id}</span><span className="min-w-0 flex-1">{l.name}<span className="block text-xs text-slate-500">{l.invoices.join(", ")}</span></span><Badge tone="blue">{l.kind === "L" ? "Lieferung" : "Leistung"}</Badge><span className="tabular">{formatEuro(l.amount_euro)}</span></li>)}
          </ul>
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="text-sm">Summe {formatEuro(data.total)}</span>
            <a className="inline-flex h-8 items-center rounded-lg bg-brand-600 px-3 text-xs font-medium text-white hover:bg-brand-700" href={`/api/reports/zm/${spec}.csv`}>CSV für BZStOnline</a>
          </div>
        </>
      )}
      {data && data.missing_vat_id.length > 0 && <p className="mt-2 text-xs text-red-600">USt-IdNr. des Kunden fehlt: {data.missing_vat_id.join(", ")}</p>}
    </Card>
  );
}
