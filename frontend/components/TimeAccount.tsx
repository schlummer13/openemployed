"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { hm } from "@/components/TimeClock";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Skeleton, Table } from "@/components/ui";
import { HttpError } from "@/lib/http";

type Fetcher = <T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown) => Promise<T>;

interface Month { month: string; target: number; actual: number; credited: number; bookings: number; diff: number; balance: number }
interface Booking { id: string; booked_on: string; minutes: number; kind: string; reason: string; payroll_period: string | null; created_by: string }
export interface Account {
  employee: { id: string; name: string; rule: "account" | "payout" | "included"; cap_hours: string | null; daily_target: number; work_days: number[]; salary_type: string };
  start: string | null; end: string; months: Month[]; bookings: Booking[]; balance: number; target: number; actual: number; over_cap?: boolean; hourly_value?: string | null;
}

const KIND: Record<string, string> = { opening: "Übertrag", payout: "Auszahlung", adjustment: "Korrektur", expiry: "Verfall" };
const RULE: Record<string, string> = { account: "Freizeitausgleich (Zeitkonto)", payout: "Auszahlung", included: "mit Gehalt abgegolten" };
const monthLabel = (m: string) => new Date(m + "-15T12:00:00").toLocaleDateString("de-DE", { month: "long", year: "numeric" });
const signed = (min: number) => (min > 0 ? "+" : "") + hm(min);
const dl = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE");

/** Arbeitszeitkonto: Saldo, Monatsübersicht, Buchungen. Mit ``manage`` (Personal) auch Übertrag/Auszahlung/Korrektur buchen. */
export function TimeAccount({ path, fetcher, manage = false, employeeId }: { path: string; fetcher: Fetcher; manage?: boolean; employeeId?: string }) {
  const [acc, setAcc] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<{ kind: string; hours: string; booked_on: string; reason: string; payroll_period: string; surcharge_percent: string } | null>(null);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => {
    fetcher<Account>("GET", path).then((a) => { setAcc(a); setError(null); }).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Zeitkonto nicht erreichbar"));
  }, [fetcher, path]);
  useEffect(load, [load]);

  if (error) return <Alert kind="info">{error}</Alert>;
  if (!acc) return <Skeleton className="h-64" />;
  const today = new Date().toISOString().slice(0, 10);
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form || !employeeId) return;
    const minutes = Math.round(Number(form.hours.replace(",", ".")) * 60);
    if (!minutes) { toast("Stunden angeben (z. B. 2,5 oder −1)", "error"); return; }
    fetcher("POST", `/api/personnel/${employeeId}/time-account/bookings`, {
      kind: form.kind, minutes: form.kind === "payout" ? Math.abs(minutes) : minutes, booked_on: form.booked_on, reason: form.reason,
      payroll_period: form.kind === "payout" ? form.payroll_period : null, surcharge_percent: Number(form.surcharge_percent) || 0,
    }).then(() => { toast(form.kind === "payout" ? "Auszahlung gebucht – erscheint im Lohnlauf." : "Gebucht."); setForm(null); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Buchen fehlgeschlagen", "error"));
  };
  const remove = async (b: Booking) => {
    if (!(await confirm({ title: "Buchung entfernen?", message: `${KIND[b.kind]} ${signed(b.minutes)} – ${b.reason}`, tone: "danger", confirmLabel: "Entfernen" }))) return;
    fetcher("DELETE", `/api/personnel/time-account/bookings/${b.id}`).then(() => { toast("Entfernt."); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  const cap = acc.employee.cap_hours ? Number(acc.employee.cap_hours) * 60 : null;
  const tone = acc.balance > 0 ? "text-emerald-600 dark:text-emerald-400" : acc.balance < 0 ? "text-rose-600 dark:text-rose-400" : "text-slate-900 dark:text-white";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Card className="px-5 py-4 sm:col-span-1">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Saldo Zeitkonto</p>
          <p className={`mt-1 font-mono text-4xl font-semibold tabular-nums ${tone}`}>{signed(acc.balance)}</p>
          <p className="mt-1 text-xs text-slate-500">{acc.start ? `seit ${dl(acc.start)}` : "noch keine Stempelzeiten"}{cap !== null && <> · Grenze {hm(cap)}</>}</p>
          {acc.over_cap && <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">Über der vereinbarten Grenze – Ausgleich oder Auszahlung planen.</p>}
        </Card>
        <Card className="px-5 py-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Soll / Ist gesamt</p>
          <p className="mt-1 font-mono text-xl font-semibold tabular-nums">{hm(acc.actual)} <span className="text-sm font-normal text-slate-500">von {hm(acc.target)}</span></p>
          <p className="mt-1 text-xs text-slate-500">Tagessoll {hm(acc.employee.daily_target)} · Urlaub/Krankheit zählen als erfüllt</p>
        </Card>
        <Card className="px-5 py-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Überstunden-Regel</p>
          <p className="mt-1 font-medium">{RULE[acc.employee.rule]}</p>
          <p className="mt-1 text-xs text-slate-500">{acc.hourly_value ? `Stundenwert ${Number(acc.hourly_value).toLocaleString("de-DE", { style: "currency", currency: "EUR" })}` : "Stundenwert: Wochenstunden fehlen"}</p>
        </Card>
      </div>

      <Card>
        <CardHeader title="Monate" description="Ist + gutgeschriebene Abwesenheiten − Soll = Differenz; Buchungen (Übertrag, Auszahlung, Verfall) fließen in den Saldo."
          actions={manage && employeeId ? <Button variant="primary" onClick={() => setForm({ kind: "payout", hours: "", booked_on: today, reason: "", payroll_period: today.slice(0, 7), surcharge_percent: "0" })}>Buchen</Button> : undefined} />
        {acc.months.length === 0 ? <div className="p-5"><Empty>Noch keine Daten – das Konto startet mit der ersten Stempelung oder dem festgelegten Startdatum.</Empty></div> : (
          <Table head={<tr><th>Monat</th><th className="text-right">Soll</th><th className="text-right">Ist</th><th className="text-right">Abwesenheit</th><th className="text-right">Differenz</th><th className="text-right">Buchungen</th><th className="text-right">Saldo</th></tr>}>
            {acc.months.slice().reverse().map((m) => (
              <tr key={m.month}>
                <td className="capitalize">{monthLabel(m.month)}</td>
                <td className="text-right font-mono tabular-nums">{hm(m.target)}</td>
                <td className="text-right font-mono tabular-nums">{hm(m.actual)}</td>
                <td className="text-right font-mono tabular-nums text-slate-500">{m.credited ? hm(m.credited) : "–"}</td>
                <td className={`text-right font-mono tabular-nums ${m.diff > 0 ? "text-emerald-600" : m.diff < 0 ? "text-rose-600" : ""}`}>{signed(m.diff)}</td>
                <td className="text-right font-mono tabular-nums text-slate-500">{m.bookings ? signed(m.bookings) : "–"}</td>
                <td className="text-right font-mono font-semibold tabular-nums">{signed(m.balance)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {acc.bookings.length > 0 && (
        <Card>
          <CardHeader title="Buchungen" />
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {acc.bookings.slice().reverse().map((b) => (
              <li key={b.id} className="flex flex-wrap items-center gap-3 px-5 py-2.5 text-sm">
                <span className="w-24 shrink-0 text-slate-500">{dl(b.booked_on)}</span>
                <Badge tone={b.kind === "payout" ? "blue" : b.kind === "expiry" ? "red" : "slate"}>{KIND[b.kind]}</Badge>
                <span className="min-w-0 flex-1 truncate">{b.reason}{b.payroll_period && <span className="text-slate-500"> · Lohnlauf {b.payroll_period}</span>}</span>
                <span className={`font-mono tabular-nums ${b.minutes > 0 ? "text-emerald-600" : "text-rose-600"}`}>{signed(b.minutes)}</span>
                {manage && <Button className="!px-2 !py-1 text-xs" onClick={() => void remove(b)}>Entfernen</Button>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Drawer open={!!form} onClose={() => setForm(null)} title="Zeitkonto buchen" subtitle={acc.employee.name}
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="ta-form">Buchen</Button></>}>
        {form && (
          <form id="ta-form" onSubmit={save} className="grid gap-4">
            <Field label="Art">
              <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                <option value="payout">Auszahlung (wird im Lohnlauf vergütet)</option>
                <option value="opening">Übertrag (z. B. aus dem Altsystem)</option>
                <option value="adjustment">Korrektur (+/−)</option>
                <option value="expiry">Verfall (−)</option>
              </Select>
            </Field>
            <Field label={form.kind === "payout" ? "Stunden auszahlen" : "Stunden (negativ = Abzug)"}><Input required inputMode="decimal" value={form.hours} onChange={(e) => setForm({ ...form, hours: e.target.value })} placeholder="z. B. 8 oder -2,5" /></Field>
            <Field label="Datum"><Input type="date" required value={form.booked_on} onChange={(e) => setForm({ ...form, booked_on: e.target.value })} /></Field>
            {form.kind === "payout" && <>
              <Field label="Abrechnungsmonat"><Input type="month" required value={form.payroll_period} onChange={(e) => setForm({ ...form, payroll_period: e.target.value })} /></Field>
              <Field label="Zuschlag %" hint="z. B. 25 % laut Vertrag; Stundenwert aus Gehalt/Stundenlohn"><Input type="number" min={0} max={100} value={form.surcharge_percent} onChange={(e) => setForm({ ...form, surcharge_percent: e.target.value })} /></Field>
            </>}
            <Field label="Begründung"><Input required minLength={3} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
          </form>
        )}
      </Drawer>
    </div>
  );
}
