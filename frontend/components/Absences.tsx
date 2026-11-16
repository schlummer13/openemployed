"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Empty, Field, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { YearCalendar } from "@/components/YearCalendar";
import { HttpError } from "@/lib/http";

type Fetcher = <T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown) => Promise<T>;

export interface Absence {
  id: string; employee_id: string; name: string; kind: string; kind_label: string; start_date: string; end_date: string;
  half_start: boolean; half_end: boolean; days: string; status: "requested" | "approved" | "rejected" | "cancelled";
  note: string | null; decision_note: string | null; decided_by: string | null; created_at: string;
}
interface Balance {
  year: number; annual: string; entitlement: string; rule: string; carryover: string; carryover_expires: string | null;
  carryover_effective: string; total: string; taken: string; planned: string; requested: string; remaining: string; available: string;
  work_days: number[]; waiting_until: string | null;
}
interface View {
  balance: Balance; next_balance: Balance; absences: Absence[]; holidays: Record<string, string>; half_days?: Record<string, string>;
  kinds: Record<string, string>; employee: { name: string; work_days: number[]; state?: string; state_name?: string };
}

export const KIND_STYLE: Record<string, string> = {
  vacation: "bg-teal-500 text-white", sick: "bg-rose-500 text-white", special: "bg-lime-600 text-white", unpaid: "bg-slate-500 text-white",
  overtime: "bg-amber-500 text-white", school: "bg-sky-500 text-white",
};
export const KIND_BORDER: Record<string, string> = {
  vacation: "border-teal-500", sick: "border-rose-500", special: "border-lime-600", unpaid: "border-slate-500", overtime: "border-amber-500",
  school: "border-sky-500",
};
export const STATUS: Record<Absence["status"], { label: string; tone: "amber" | "green" | "red" | "slate" }> = {
  requested: { label: "beantragt", tone: "amber" }, approved: { label: "genehmigt", tone: "green" },
  rejected: { label: "abgelehnt", tone: "red" }, cancelled: { label: "storniert", tone: "slate" },
};
const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
export const num = (v: string | number) => Number(v).toLocaleString("de-DE", { maximumFractionDigits: 1 });
export const dLabel = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });

/** Arbeitstage im Zeitraum (wie Backend): nur eigene Wochentage, ohne Feiertage, halbe Tage am Rand und halbe freie Tage (Heiligabend …). */
export function countDays(start: string, end: string, workDays: number[], holidays: Record<string, string>, halfStart: boolean, halfEnd: boolean,
  halfDays: Record<string, string> = {}): number {
  if (!start || !end || end < start) return 0;
  let n = 0;
  const d = new Date(start + "T12:00:00");
  const last = new Date(end + "T12:00:00");
  while (d <= last) {
    const iso = d.toISOString().slice(0, 10);
    const wd = (d.getDay() + 6) % 7;
    if (workDays.includes(wd) && !holidays[iso]) {
      const day = (iso === start && halfStart) || (iso === end && halfEnd) ? 0.5 : 1;
      n += Math.max(0, day - (halfDays[iso] ? 0.5 : 0));
    }
    d.setDate(d.getDate() + 1);
  }
  return n;
}

export function MyAbsences({ base, fetcher }: { base: string; fetcher: Fetcher }) {
  const [v, setV] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ kind: "vacation", start_date: "", end_date: "", half_start: false, half_end: false, note: "" });
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();

  const load = useCallback(() => {
    fetcher<View>("GET", `${base}/absences`).then(setV).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Nicht erreichbar"));
  }, [base, fetcher]);
  useEffect(load, [load]);

  const days = useMemo(() => v ? countDays(form.start_date, form.end_date || form.start_date, v.employee.work_days, v.holidays, form.half_start, form.half_end, v.half_days) : 0,
    [v, form]);
  const [calYear, setCalYear] = useState(() => new Date().getFullYear());

  if (error) return <Alert kind="info">{error}</Alert>;
  if (!v) return <div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-64" /><Skeleton className="h-64" /></div>;

  const yearOf = form.start_date ? Number(form.start_date.slice(0, 4)) : v.balance.year;
  const bal = yearOf === v.next_balance.year ? v.next_balance : v.balance;
  const tooMuch = form.kind === "vacation" && days > Number(bal.available);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    fetcher("POST", `${base}/absences`, { ...form, end_date: form.end_date || form.start_date, note: form.note || null })
      .then(() => {
        toast(form.kind === "sick" ? "Krankmeldung eingetragen. Gute Besserung!" : "Antrag gestellt – die Geschäftsführung wird benachrichtigt.");
        setForm({ ...form, start_date: "", end_date: "", half_start: false, half_end: false, note: "" });
        load();
      })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Antrag fehlgeschlagen", "error"))
      .finally(() => setBusy(false));
  };
  const cancel = async (a: Absence) => {
    if (!(await confirm({ title: "Antrag zurückziehen?", message: `${a.kind_label} ${dLabel(a.start_date)} – ${dLabel(a.end_date)}`, confirmLabel: "Zurückziehen", tone: "danger" }))) return;
    fetcher("POST", `${base}/absences/${a.id}/cancel`).then(() => { toast("Zurückgezogen."); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  const upcoming = v.absences.filter((a) => a.end_date >= new Date().toISOString().slice(0, 10)).sort((a, b) => a.start_date.localeCompare(b.start_date));
  const past = v.absences.filter((a) => !upcoming.includes(a)).sort((a, b) => b.start_date.localeCompare(a.start_date)).slice(0, 12);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-6">
        <BalanceCard b={v.balance} />
        {Number(v.next_balance.total) > 0 && v.next_balance.year !== v.balance.year && (
          <p className="text-xs text-slate-500">Anspruch {v.next_balance.year}: {num(v.next_balance.entitlement)} Tage ({v.next_balance.rule})</p>
        )}
      </div>
      <div className="space-y-6">
        <Card>
          <CardHeader title="Abwesenheit beantragen" description={`Deine Arbeitstage: ${v.employee.work_days.map((d) => WD[d]).join(", ")} – Wochenenden und Feiertage zählen nicht.`} />
          <form onSubmit={submit} className="grid gap-3 px-5 pb-5 sm:grid-cols-2">
            <Field label="Art" className="sm:col-span-2">
              <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                {Object.entries(v.kinds).filter(([k]) => k !== "school").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Von"><Input type="date" required value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value, end_date: form.end_date && form.end_date < e.target.value ? e.target.value : form.end_date })} /></Field>
            <Field label="Bis"><Input type="date" min={form.start_date} value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300"><input type="checkbox" checked={form.half_start} onChange={(e) => setForm({ ...form, half_start: e.target.checked })} /> erster Tag halb</label>
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300"><input type="checkbox" checked={form.half_end} disabled={!form.end_date || form.end_date === form.start_date} onChange={(e) => setForm({ ...form, half_end: e.target.checked })} /> letzter Tag halb</label>
            <Field label="Notiz (optional)" className="sm:col-span-2"><Textarea rows={2} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></Field>
            <div className="flex flex-wrap items-center justify-between gap-3 sm:col-span-2">
              <p className={`text-sm ${tooMuch ? "text-rose-600" : "text-slate-600 dark:text-slate-300"}`}>
                {form.start_date ? <><strong className="font-semibold">{num(days)}</strong> Arbeitstag{days === 1 ? "" : "e"}
                  {form.kind === "vacation" && <> · danach {num(Number(bal.available) - days)} verfügbar</>}</> : "Zeitraum wählen"}
              </p>
              <Button variant="primary" type="submit" disabled={busy || !form.start_date || tooMuch}>{form.kind === "sick" ? "Krankmeldung eintragen" : "Beantragen"}</Button>
            </div>
          </form>
        </Card>
        <Card>
          <CardHeader title="Meine Anträge" />
          {upcoming.length + past.length === 0 ? <div className="p-5"><Empty>Noch keine Abwesenheiten.</Empty></div> : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {[...upcoming, ...past].map((a) => (
                <li key={a.id} className={`flex flex-wrap items-center gap-3 px-5 py-3 text-sm ${past.includes(a) ? "opacity-70" : ""}`}>
                  <span className={`size-2.5 shrink-0 rounded-full ${KIND_STYLE[a.kind]?.split(" ")[0] ?? "bg-slate-400"}`} />
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-slate-900 dark:text-slate-100">{a.kind_label} · {num(a.days)} Tag{Number(a.days) === 1 ? "" : "e"}</p>
                    <p className="text-xs text-slate-500">{dLabel(a.start_date)}{a.end_date !== a.start_date && <> – {dLabel(a.end_date)}</>}{a.decision_note && <> · „{a.decision_note}“</>}</p>
                  </div>
                  <Badge tone={STATUS[a.status].tone}>{STATUS[a.status].label}</Badge>
                  {(a.status === "requested" || (a.status === "approved" && upcoming.includes(a) && a.start_date > new Date().toISOString().slice(0, 10))) && (
                    <Button onClick={() => void cancel(a)} className="!px-2 !py-1 text-xs">Zurückziehen</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card className="lg:col-span-2">
        <CardHeader title={`Mein Kalender ${calYear}`}
          description={`Feiertage${v.employee.state_name ? ` in ${v.employee.state_name}` : ""}, betriebliche freie Tage und deine Abwesenheiten`}
          actions={<div className="flex items-center gap-1">
            <Button onClick={() => setCalYear(calYear - 1)} disabled={calYear <= v.balance.year - 1} aria-label="Vorjahr" className="!px-2.5">‹</Button>
            <Button onClick={() => setCalYear(calYear + 1)} disabled={calYear >= v.balance.year + 1} aria-label="Folgejahr" className="!px-2.5">›</Button>
          </div>} />
        <div className="px-5 pb-5">
          <YearCalendar year={calYear} holidays={v.holidays} halfDays={v.half_days} absences={v.absences} workDays={v.employee.work_days} />
        </div>
      </Card>
    </div>
  );
}

export function BalanceCard({ b }: { b: Balance }) {
  const total = Number(b.total) || 1;
  const seg = (v: string) => `${Math.max(0, Math.min(100, (Number(v) / total) * 100))}%`;
  return (
    <Card>
      <CardHeader title={`Urlaubskonto ${b.year}`} description={b.rule} />
      <div className="px-5 pb-5">
        <div className="flex items-end gap-2">
          <span className="text-5xl font-semibold tracking-tight text-slate-900 tabular-nums dark:text-white">{num(b.available)}</span>
          <span className="pb-1.5 text-sm text-slate-500">von {num(b.total)} Tagen verfügbar</span>
        </div>
        <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden>
          <div className="bg-slate-400" style={{ width: seg(b.taken) }} />
          <div className="bg-teal-500" style={{ width: seg(b.planned) }} />
          <div className="bg-amber-400" style={{ width: seg(b.requested) }} />
        </div>
        <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm 2xl:grid-cols-2">
          <Row label="Jahresanspruch (Vertrag)" value={b.annual} />
          <Row label={`Anspruch ${b.year}`} value={b.entitlement} />
          {Number(b.carryover) > 0 && <Row label={`Resturlaub Vorjahr${b.carryover_expires ? ` (bis ${dLabel(b.carryover_expires)})` : ""}`} value={b.carryover_effective} />}
          <Row label="genommen" value={b.taken} dot="bg-slate-400" />
          <Row label="geplant (genehmigt)" value={b.planned} dot="bg-teal-500" />
          <Row label="beantragt" value={b.requested} dot="bg-amber-400" />
        </dl>
        {b.waiting_until && new Date(b.waiting_until) > new Date() && (
          <p className="mt-3 text-xs text-slate-500">Wartezeit (§ 4 BUrlG) bis {dLabel(b.waiting_until)} – Urlaub kann trotzdem schon beantragt werden.</p>
        )}
      </div>
    </Card>
  );
}

function Row({ label, value, dot }: { label: string; value: string; dot?: string }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-1.5 dark:border-slate-800">
      <dt className="flex min-w-0 items-center gap-1.5 truncate text-slate-500">{dot && <span className={`size-2 shrink-0 rounded-full ${dot}`} />}{label}</dt>
      <dd className="shrink-0 font-mono tabular-nums text-slate-900 dark:text-slate-100">{num(value)}</dd>
    </div>
  );
}
