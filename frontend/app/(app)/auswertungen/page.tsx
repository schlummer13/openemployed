"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, PageHeader, PageSkeleton, Select } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api } from "@/lib/http";

interface Row { key: string; label: string; sum: boolean; month: string; month_pct: string | null; ytd: string; ytd_pct: string | null; prev_month: string; prev_ytd: string; delta_month: string; delta_ytd: string }
interface Bwa { period: string; rows: Row[]; trend: { month: string; output: string; costs: string; result: string }[]; note: string }

const label = (p: string) => new Date(`${p}-01T00:00:00`).toLocaleDateString("de-DE", { month: "long", year: "numeric" });
const short = (p: string) => new Date(`${p}-01T00:00:00`).toLocaleDateString("de-DE", { month: "short" });
const lastMonth = () => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7); };

function Delta({ v }: { v: string }) {
  const n = Number(v);
  if (!n) return <span className="text-slate-400">–</span>;
  return <span className={n > 0 ? "text-emerald-600" : "text-red-600"}>{n > 0 ? "+" : "−"}{formatEuro(Math.abs(n))}</span>;
}

export default function BwaPage() {
  const [period, setPeriod] = useState(lastMonth());
  const [data, setData] = useState<Bwa | null>(null);
  useEffect(() => { setData(null); void api<Bwa>("GET", `/api/reports/bwa/${period}`).then(setData); }, [period]);
  const months = Array.from({ length: 24 }, (_, i) => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - i); return d.toISOString().slice(0, 7); });
  const max = data ? Math.max(1, ...data.trend.flatMap((t) => [Number(t.output), Number(t.costs)])) : 1;
  return (
    <>
      <PageHeader title="Auswertungen" eyebrow="Finanzen" description="Betriebswirtschaftliche Auswertung (BWA) je Monat mit Jahreswerten und Vorjahresvergleich – wie vom Steuerberater, nur jederzeit aktuell."
        actions={<Select aria-label="Monat" value={period} onChange={(e) => setPeriod(e.target.value)} className="!w-48">{months.map((m) => <option key={m} value={m}>{label(m)}</option>)}</Select>} />
      {!data ? <PageSkeleton stats={0} /> : (
        <div className="grid gap-6">
          <Card>
            <CardHeader title="Entwicklung der letzten 12 Monate" description="Gesamtleistung, Kosten und Ergebnis" />
            <div className="flex h-44 items-end gap-1.5 sm:gap-3" role="img" aria-label="Monatsverlauf Leistung und Kosten">
              {data.trend.map((t) => (
                <div key={t.month} className="flex min-w-0 flex-1 flex-col items-center gap-1">
                  <div className="flex h-36 w-full items-end justify-center gap-0.5">
                    <div className="w-1/2 max-w-4 rounded-t bg-brand-500" style={{ height: `${(Number(t.output) / max) * 100}%` }} title={`Leistung ${formatEuro(t.output)}`} />
                    <div className="w-1/2 max-w-4 rounded-t bg-slate-300 dark:bg-slate-600" style={{ height: `${(Number(t.costs) / max) * 100}%` }} title={`Kosten ${formatEuro(t.costs)}`} />
                  </div>
                  <span className="truncate text-[10px] text-slate-500">{short(t.month)}</span>
                </div>
              ))}
            </div>
            <p className="mt-2 flex gap-4 text-xs text-slate-500"><span className="flex items-center gap-1.5"><span className="size-2 rounded-sm bg-brand-500" />Gesamtleistung</span><span className="flex items-center gap-1.5"><span className="size-2 rounded-sm bg-slate-300 dark:bg-slate-600" />Kosten</span></p>
          </Card>
          <Card>
            <CardHeader title={`BWA ${label(period)}`} description={data.note} />
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm tabular">
                <thead className="border-b border-slate-200 text-right text-xs text-slate-500 dark:border-white/10">
                  <tr><th className="pb-2 text-left font-medium">Position</th><th className="pb-2 font-medium">Monat</th><th className="pb-2 font-medium">%</th><th className="pb-2 font-medium">Vorjahresmonat</th><th className="pb-2 font-medium">Abw.</th><th className="pb-2 font-medium">Jan.–{short(period)}</th><th className="pb-2 font-medium">%</th><th className="pb-2 font-medium">Vorjahr</th><th className="pb-2 font-medium">Abw.</th></tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.key} className={`border-b border-slate-100 dark:border-white/5 ${r.sum ? "bg-slate-50 font-semibold dark:bg-white/[0.03]" : ""}`}>
                      <td className="py-1.5 pr-3">{r.label}</td>
                      <td className="py-1.5 text-right">{formatEuro(r.month)}</td>
                      <td className="py-1.5 text-right text-xs text-slate-500">{r.month_pct ? r.month_pct.replace(".", ",") : ""}</td>
                      <td className="py-1.5 text-right text-slate-500">{formatEuro(r.prev_month)}</td>
                      <td className="py-1.5 text-right text-xs"><Delta v={["material", "personnel", "rooms", "operating_taxes", "insurance", "vehicles", "marketing", "delivery", "depreciation", "repairs", "other_costs", "total_costs", "interest", "income_taxes"].includes(r.key) ? String(-Number(r.delta_month)) : r.delta_month} /></td>
                      <td className="py-1.5 text-right">{formatEuro(r.ytd)}</td>
                      <td className="py-1.5 text-right text-xs text-slate-500">{r.ytd_pct ? r.ytd_pct.replace(".", ",") : ""}</td>
                      <td className="py-1.5 text-right text-slate-500">{formatEuro(r.prev_ytd)}</td>
                      <td className="py-1.5 text-right text-xs"><Delta v={["material", "personnel", "rooms", "operating_taxes", "insurance", "vehicles", "marketing", "delivery", "depreciation", "repairs", "other_costs", "total_costs", "interest", "income_taxes"].includes(r.key) ? String(-Number(r.delta_ytd)) : r.delta_ytd} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-slate-500">Abweichungen grün = besser als im Vorjahr (mehr Erlös bzw. weniger Kosten). Prozent = Anteil an der Gesamtleistung.</p>
          </Card>
        </div>
      )}
    </>
  );
}
