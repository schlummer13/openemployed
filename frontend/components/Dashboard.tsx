"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Avatar, type Person } from "@/components/Avatar";
import { Icon } from "@/components/Icon";
import { RelDate } from "@/components/RelDate";
import { SetupCard } from "@/components/SetupCard";
import { ClockChip } from "@/components/TimeClock";
import { Badge, Button, Card, Input, PageSkeleton, Stat } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError, todayIso } from "@/lib/http";

interface Task { severity: "red" | "amber" | "blue"; title: string; href: string; detail: string | null }
interface Month { month: string; income: string; expenses: string; salaries: string; cash_in: string; result: string }
interface ProjectTask { id: string; title: string; priority: "dringend" | "hoch" | "mittel" | "niedrig"; due_date: string | null; project_id: string; project_name: string; project_color: string; assignee_id: string | null; assignee_name: string | null; assignee_color: string | null; assignee_photo: boolean }
interface Capital {
  balance: { amount: string; as_of: string | null; source: string; estimated: boolean };
  vat_due: string; income_tax_reserve: string; payables: string; salaries_30d: string; fixed_costs_30d: string;
  reserved: string; available: string; min_liquidity: string; below_minimum: boolean; ug_reserve_note: string;
}
interface ForecastMonth { month: string; receivables: string; contracts: string; salaries: string; fixed_costs: string; payables: string; vat: string; inflow: string; outflow: string; net: string; closing: string }
interface Forecast { start: string; estimated: boolean; months: ForecastMonth[]; min_liquidity: string; lowest: string }
interface DashboardData {
  kpis: { open_receivables: string; overdue: string; open_count: number; mrr: string; revenue_month: string; expenses_month: string };
  tasks: Task[]; capital: Capital; months: Month[]; project_tasks: ProjectTask[]; shareholders: Person[]; forecast: Forecast;
}

const DOT = { red: "bg-red-500", amber: "bg-amber-500", blue: "bg-brand-500" };
const PRIO: Record<ProjectTask["priority"], "red" | "amber" | "blue" | "slate"> = { dringend: "red", hoch: "amber", mittel: "blue", niedrig: "slate" };
const MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api<DashboardData>("GET", "/api/dashboard").then(setData).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen")); }, []);
  useEffect(load, [load]);
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!data) return <PageSkeleton />;
  const k = data.kpis;
  const thisMonth = data.months[data.months.length - 1];
  const prevMonth = data.months[data.months.length - 2];
  const pct = (now?: string, before?: string) => {
    const a = Number(now ?? 0), b = Number(before ?? 0);
    return b === 0 ? null : ((a - b) / Math.abs(b)) * 100;
  };

  return (
    <div className="space-y-6">
      <SetupCard />
      <ClockChip fetcher={api} />
      <CapitalCard capital={data.capital} onSaved={load} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Einnahmen diesen Monat" icon="trend" tone="green" value={formatEuro(thisMonth?.income ?? "0")} trend={{ pct: pct(thisMonth?.income, prevMonth?.income), good: "up" }} hint={`Zahlungseingänge ${formatEuro(thisMonth?.cash_in ?? "0")}`} />
        <Stat label="Ausgaben diesen Monat" icon="receipt" tone="amber" value={formatEuro(thisMonth?.expenses ?? "0")} trend={{ pct: pct(thisMonth?.expenses, prevMonth?.expenses), good: "down" }} hint={Number(thisMonth?.salaries ?? 0) > 0 ? `davon Gehälter ${formatEuro(thisMonth?.salaries ?? "0")}` : "laut gebuchten Belegen"} />
        <Stat label="MRR (wiederkehrend)" icon="repeat" tone="brand" value={formatEuro(k.mrr)} hint={`ARR ${formatEuro((Number(k.mrr) * 12).toFixed(2))}`} />
        <Stat label="Offene Forderungen" icon="alarm" tone={Number(k.overdue) > 0 ? "red" : "slate"} value={formatEuro(k.open_receivables)}
          hint={Number(k.overdue) > 0 ? `${formatEuro(k.overdue)} überfällig` : `${k.open_count} offene Rechnung(en)`} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <MonthsChart months={data.months} />
        <ForecastCard forecast={data.forecast} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="!p-0">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
            <div><h2 className="font-semibold">Projekte & Aufgaben</h2><p className="text-xs text-slate-500">Nach Priorität und Fälligkeit</p></div>
            <Link href="/projekte" className="text-sm font-medium text-brand-700 hover:underline dark:text-brand-300">Alle Projekte</Link>
          </div>
          {data.project_tasks.length === 0 ? <p className="px-5 py-6 text-sm text-slate-500">Keine offenen Aufgaben.</p> : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.project_tasks.map((t) => {
                return (
                  <li key={t.id}>
                    <Link href="/projekte" className="flex items-center gap-3 px-5 py-2.5 transition hover:bg-slate-50 dark:hover:bg-slate-800/50">
                      <Avatar person={t.assignee_id ? { id: t.assignee_id, name: t.assignee_name ?? "", color: t.assignee_color, has_photo: t.assignee_photo } : null} size={26} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{t.title}</span>
                        <span className="flex items-center gap-1.5 text-xs text-slate-500"><span className="size-2 rounded-full" style={{ background: t.project_color }} />{t.project_name}{t.due_date && <> · <RelDate date={t.due_date} mode="due" /></>}</span>
                      </span>
                      <Badge tone={PRIO[t.priority]}>{t.priority}</Badge>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card className="!p-0">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
            <div><h2 className="font-semibold">Zu erledigen <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 dark:bg-white/5 dark:text-slate-300">{data.tasks.length}</span></h2><p className="text-xs text-slate-500">Automatisch ermittelt: Fristen, Belege, Forderungen</p></div>
            <Link href="/aufgaben" className="text-sm font-medium text-brand-700 hover:underline dark:text-brand-300">Alle anzeigen</Link>
          </div>
          {data.tasks.length === 0 ? (
            <div className="flex items-center gap-3 px-5 py-6 text-sm"><span className="grid size-9 place-items-center rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400"><Icon name="check" /></span>Alles erledigt.</div>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.tasks.slice(0, 7).map((task) => (
                <li key={task.title}>
                  <Link href={task.href} className="group flex items-center gap-3 px-5 py-2.5 transition hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <span className={`size-2 shrink-0 rounded-full ${DOT[task.severity]}`} />
                    <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{task.title}</span>{task.detail && <span className="block text-xs text-slate-500">{task.detail}</span>}</span>
                    <Icon name="arrow" className="size-4 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-500" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function CapitalCard({ capital: c, onSaved }: { capital: Capital; onSaved: () => void }) {
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ balance_on: todayIso(), amount: "" });
  const [error, setError] = useState<string | null>(null);
  const rows: [string, string, string][] = [
    ["Umsatzsteuer (offene Voranmeldungen)", c.vat_due, "an das Finanzamt"],
    ["Ertragsteuern laufendes Jahr", c.income_tax_reserve, "KSt, Soli, GewSt (Rücklage)"],
    ["Offene Lieferantenrechnungen", c.payables, "gebucht, noch nicht bezahlt"],
    ["Gehälter nächste 30 Tage", c.salaries_30d, "Arbeitgeberkosten"],
    ["Fixkosten nächste 30 Tage", c.fixed_costs_30d, "laut Einstellungen"],
  ];
  const save = (e: FormEvent) => {
    e.preventDefault(); setError(null);
    api("POST", "/api/bank/balance", { balance_on: form.balance_on, amount: toApiDecimal(form.amount) })
      .then(() => { setEdit(false); onSaved(); }).catch((err: unknown) => setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen"));
  };
  return (
    <Card className="overflow-hidden !p-0">
      <div className="grid lg:grid-cols-[1.1fr_1fr]">
        <div className={`p-6 ${c.below_minimum ? "bg-gradient-to-br from-red-50 to-white dark:from-red-950/40 dark:to-slate-900" : "bg-gradient-to-br from-brand-50 to-white dark:from-brand-950/50 dark:to-slate-900"}`}>
          <p className="text-sm font-medium text-slate-600 dark:text-slate-300">Frei verfügbares Kapital</p>
          <p className={`mt-1 text-4xl font-semibold tracking-tight tabular ${c.below_minimum ? "text-red-700 dark:text-red-300" : ""}`}>{formatEuro(c.available)}</p>
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
            Kontostand {formatEuro(c.balance.amount)} {c.balance.estimated ? <span className="text-amber-700 dark:text-amber-300">(geschätzt aus der Buchhaltung)</span> : <span className="text-slate-500">(Stand {formatDate(c.balance.as_of)} + spätere Umsätze)</span>}
          </p>
          <p className="mt-1 text-xs text-slate-500">Mindestreserve {formatEuro(c.min_liquidity)}{c.below_minimum ? " – unterschritten!" : ""}</p>
          {!edit ? <Button className="mt-4" onClick={() => setEdit(true)}>Kontostand erfassen</Button> : (
            <form onSubmit={save} className="mt-4 flex flex-wrap items-end gap-2">
              <Input type="date" aria-label="Stand vom" max={todayIso()} value={form.balance_on} onChange={(e) => setForm({ ...form, balance_on: e.target.value })} className="w-40" />
              <Input aria-label="Kontostand in Euro" required inputMode="decimal" placeholder="Kontostand €" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="w-40" />
              <Button type="submit" variant="primary">Speichern</Button><Button variant="ghost" onClick={() => setEdit(false)}>Abbrechen</Button>
              {error && <p className="w-full text-xs text-red-600">{error}</p>}
            </form>
          )}
        </div>
        <div className="border-t border-slate-100 p-6 lg:border-l lg:border-t-0 dark:border-slate-800">
          <p className="mb-2 text-sm font-medium text-slate-600 dark:text-slate-300">Davon gebunden: {formatEuro(c.reserved)}</p>
          <ul className="space-y-2 text-sm">
            {rows.map(([label, value, hint]) => (
              <li key={label} className="flex items-baseline justify-between gap-3">
                <span><span className="block">{label}</span><span className="block text-xs text-slate-500">{hint}</span></span>
                <span className="shrink-0 whitespace-nowrap tabular font-medium">− {formatEuro(value)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-800">
            Gesetzliche UG-Rücklage {new Date().getFullYear()}: <span className="font-medium text-slate-700 dark:text-slate-300">{formatEuro(c.ug_reserve_note)}</span> – 25 % des Jahresüberschusses (§ 5a Abs. 3 GmbHG). Kein Geldabfluss, aber nicht ausschüttbar.
          </p>
        </div>
      </div>
    </Card>
  );
}

function MonthsChart({ months }: { months: Month[] }) {
  const max = Math.max(1, ...months.flatMap((m) => [Number(m.income), Number(m.expenses)]));
  const nice = (() => { const p = 10 ** Math.floor(Math.log10(max)); return Math.ceil(max / p) * p; })();
  const totalIn = months.reduce((s, m) => s + Number(m.income), 0);
  const totalOut = months.reduce((s, m) => s + Number(m.expenses), 0);
  const compact = (v: number) => v >= 1000 ? `${(v / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} T€` : `${v.toLocaleString("de-DE")} €`;
  return (
    <Card>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="font-semibold">Einnahmen und Ausgaben</h2><p className="text-xs text-slate-500">Letzte 12 Monate · netto, Ausgaben inkl. Gehälter</p></div>
        <div className="flex flex-wrap gap-4 text-xs">
          <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-emerald-500" />Einnahmen <strong className="tabular">{formatEuro(totalIn.toFixed(2))}</strong></span>
          <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-amber-500" />Ausgaben <strong className="tabular">{formatEuro(totalOut.toFixed(2))}</strong></span>
        </div>
      </div>
      <div className="flex gap-3">
        <div className="flex h-48 flex-col justify-between pb-5 text-right text-[10px] tabular text-slate-400">
          {[1, 0.5, 0].map((f) => <span key={f}>{compact(nice * f)}</span>)}
        </div>
        <div className="relative flex h-48 flex-1 items-end gap-1 sm:gap-2">
          <div className="pointer-events-none absolute inset-x-0 top-0 bottom-5 flex flex-col justify-between">
            {[0, 1, 2].map((i) => <div key={i} className="border-t border-dashed border-slate-200 dark:border-white/10" />)}
          </div>
          {months.map((m, i) => {
            const [y, mm] = m.month.split("-");
            const last = i === months.length - 1;
            const side = i < 2 ? "left-0" : i > months.length - 3 ? "right-0" : "left-1/2 -translate-x-1/2";
            return (
              <div key={m.month} className="group relative flex h-full flex-1 flex-col justify-end">
                <div className="absolute inset-x-0 top-0 bottom-5 rounded-md transition group-hover:bg-slate-100/70 dark:group-hover:bg-white/[0.04]" />
                <div className="relative flex h-full items-end justify-center gap-0.5 pb-5">
                  <div className={`w-1/2 max-w-3.5 rounded-t-[3px] bg-emerald-500 transition ${last ? "" : "opacity-80 group-hover:opacity-100"}`} style={{ height: `${(Number(m.income) / nice) * 100}%` }} />
                  <div className={`w-1/2 max-w-3.5 rounded-t-[3px] bg-amber-500 transition ${last ? "" : "opacity-80 group-hover:opacity-100"}`} style={{ height: `${(Number(m.expenses) / nice) * 100}%` }} />
                </div>
                <span className={`absolute inset-x-0 bottom-0 text-center text-[10px] ${last ? "font-semibold text-slate-700 dark:text-slate-200" : "text-slate-500"}`}>{MONTHS[Number(mm) - 1]}</span>
                <div className={`pointer-events-none absolute bottom-full z-10 mb-1 hidden w-max rounded-lg bg-slate-900 px-3 py-2 text-xs text-white shadow-pop group-hover:block dark:bg-white dark:text-slate-900 ${side}`}>
                  <p className="mb-1 font-semibold">{MONTHS[Number(mm) - 1]} {y}</p>
                  <p className="flex justify-between gap-4"><span>Einnahmen</span><span className="tabular">{formatEuro(m.income)}</span></p>
                  <p className="flex justify-between gap-4"><span>Ausgaben</span><span className="tabular">{formatEuro(m.expenses)}</span></p>
                  <p className="mt-1 flex justify-between gap-4 border-t border-white/20 pt-1 font-medium dark:border-slate-900/20"><span>Ergebnis</span><span className="tabular">{formatEuro(m.result)}</span></p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
}

function ForecastCard({ forecast: f }: { forecast: Forecast }) {
  const min = Number(f.min_liquidity);
  const critical = Number(f.lowest) < min;
  const max = Math.max(1, ...f.months.map((m) => Math.abs(Number(m.closing))), Math.abs(Number(f.start)));
  return (
    <Card>
      <div className="mb-4">
        <h2 className="font-semibold">Liquiditätsvorschau</h2>
        <p className="text-xs text-slate-500">Kontostand am Monatsende, geplant aus Rechnungen, Verträgen, Gehältern und Fixkosten</p>
      </div>
      {critical && <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-500/10 dark:text-red-300">Mindestreserve von {formatEuro(f.min_liquidity)} wird voraussichtlich unterschritten.</p>}
      <ul className="space-y-3">
        {f.months.map((m) => {
          const [y, mm] = m.month.split("-");
          const closing = Number(m.closing);
          return (
            <li key={m.month} className="group">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="font-medium">{MONTHS[Number(mm) - 1]} {y}</span>
                <span className={`tabular font-semibold ${closing < min ? "text-red-600 dark:text-red-400" : ""}`}>{formatEuro(m.closing)}</span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/5">
                <div className={`h-full rounded-full ${closing < min ? "bg-red-500" : "bg-brand-500"}`} style={{ width: `${Math.max(0, Math.min(100, (closing / max) * 100))}%` }} />
              </div>
              <p className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
                <span className="text-emerald-700 dark:text-emerald-400">+ {formatEuro(m.inflow)}</span>
                <span className="text-amber-700 dark:text-amber-400">− {formatEuro(m.outflow)}</span>
                <span className="hidden group-hover:inline">Gehälter {formatEuro(m.salaries)} · Fixkosten {formatEuro(m.fixed_costs)}{Number(m.vat) ? ` · USt ${formatEuro(m.vat)}` : ""}{Number(m.payables) ? ` · Lieferanten ${formatEuro(m.payables)}` : ""}</span>
              </p>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 border-t border-slate-100 pt-3 text-[11px] leading-relaxed text-slate-500 dark:border-white/5">
        Start: {formatEuro(f.start)}{f.estimated ? " (geschätzt)" : ""}. Planung ohne Ertragsteuer-Vorauszahlungen; Zahlungseingang 14 Tage nach Abrechnung angenommen.
      </p>
    </Card>
  );
}
