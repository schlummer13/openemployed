"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useUser } from "@/components/AuthGate";
import { useFeedback } from "@/components/Feedback";
import { YearCalendar } from "@/components/YearCalendar";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Field, Input, PageHeader, PageSkeleton, Select, Stat } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

interface Item { date: string; name: string; kind: "statutory" | "regional" | "company"; half_day: boolean; weekday: string; on_weekend: boolean }
interface Rule { id: string; name: string; rule: "fixed" | "easter"; month: number | null; day: number | null; easter_offset: number | null; year: number | null; half_day: boolean }
interface Suggestion { name: string; rule: "fixed" | "easter"; month?: number; day?: number; easter_offset?: number; half_day: boolean; date: string | null }
interface Cal {
  year: number; state: string; state_name: string; states: Record<string, string>; items: Item[]; easter: string; statutory_count: number; weekday_count: number;
  bridges: { date: string; free_from: string; free_to: string; days_off: number }[]; months: { month: number; workdays: string }[]; workdays: string;
  rules: Rule[]; suggestions: Suggestion[];
}

const KIND: Record<Item["kind"], { label: string; tone: "amber" | "blue" | "green" }> = {
  statutory: { label: "gesetzlich", tone: "amber" }, regional: { label: "regional", tone: "blue" }, company: { label: "betrieblich", tone: "green" },
};
const MONTH_SHORT = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
const dl = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
const dshort = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
const num = (v: string | number) => Number(v).toLocaleString("de-DE", { maximumFractionDigits: 1 });
const STATE_KEY = "oe.holiday-state";
function ruleText(r: Rule | Suggestion): string {
  if (r.rule === "easter") {
    const o = r.easter_offset ?? 0;
    return o === 0 ? "Ostersonntag" : `${Math.abs(o)} Tage ${o < 0 ? "vor" : "nach"} Ostern`;
  }
  return `jedes Jahr am ${String(r.day).padStart(2, "0")}.${String(r.month).padStart(2, "0")}.`;
}

export default function FeiertagePage() {
  const me = useUser();
  const canEdit = me?.permissions?.payroll === "write";
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [state, setState] = useState("NW");
  const [cal, setCal] = useState<Cal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [add, setAdd] = useState<{ name: string; rule: "fixed" | "easter"; date: string; easter_offset: string; once: boolean; half_day: boolean } | null>(null);
  const [ical, setIcal] = useState({ start: thisYear, end: thisYear + 4 });
  const { toast, confirm } = useFeedback();

  useEffect(() => { try { const s = localStorage.getItem(STATE_KEY); if (s) setState(s); } catch { /* egal */ } }, []);
  const load = useCallback(() => {
    api<Cal>("GET", `/api/holidays?year=${year}&state=${state}`).then((c) => { setCal(c); setError(null); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Feiertage nicht erreichbar"));
  }, [year, state]);
  useEffect(load, [load]);

  const maps = useMemo(() => {
    const holidays: Record<string, string> = {}, half: Record<string, string> = {}, regional: Record<string, string> = {};
    for (const i of cal?.items ?? []) {
      if (i.kind === "regional") regional[i.date] = i.name;
      else if (i.half_day) half[i.date] = i.name;
      else holidays[i.date] = holidays[i.date] ? `${holidays[i.date]} · ${i.name}` : i.name;
    }
    return { holidays, half, regional };
  }, [cal]);

  const chooseState = (s: string) => { setState(s); try { localStorage.setItem(STATE_KEY, s); } catch { /* egal */ } };

  const quickAdd = (s: Suggestion) => {
    api("POST", "/api/holidays/company", { name: s.name, rule: s.rule, month: s.month ?? null, day: s.day ?? null, easter_offset: s.easter_offset ?? null, half_day: s.half_day })
      .then(() => { toast(`${s.name} ist jetzt ${s.half_day ? "halber" : "ganzer"} freier Tag – in allen Jahren.`); load(); })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  };
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!add) return;
    const [y, m, d] = add.date.split("-").map(Number);
    const body = add.rule === "fixed"
      ? { name: add.name, rule: "fixed", month: m, day: d, year: add.once ? y : null, half_day: add.half_day }
      : { name: add.name, rule: "easter", easter_offset: Number(add.easter_offset), year: add.once ? year : null, half_day: add.half_day };
    api("POST", "/api/holidays/company", body).then(() => { toast("Freier Tag angelegt."); setAdd(null); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  const remove = async (r: Rule) => {
    if (!(await confirm({ title: `„${r.name}“ entfernen?`, message: "Der Tag zählt danach wieder als Arbeitstag – auch in bereits gestellten Urlaubsanträgen ab jetzt.", confirmLabel: "Entfernen", tone: "danger" }))) return;
    api("DELETE", `/api/holidays/company/${r.id}`).then(() => { toast("Entfernt."); load(); })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  };

  if (error) return <Alert>{error}</Alert>;
  if (!cal) return <PageSkeleton stats={4} rows={6} />;
  const upcoming = cal.items.find((i) => i.kind !== "regional" && i.date >= new Date().toISOString().slice(0, 10));

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Team" title="Feiertage" description="Gesetzliche Feiertage je Bundesland – für jedes Jahr vorausberechnet, mit betrieblichen freien Tagen und Brückentagen."
        actions={canEdit ? <Button variant="primary" onClick={() => setAdd({ name: "", rule: "fixed", date: `${year}-12-24`, easter_offset: "-48", once: false, half_day: false })}>Freien Tag hinzufügen</Button> : undefined} />

      <Card className="flex flex-wrap items-end gap-3 px-4 py-3">
        <div className="flex items-center gap-1">
          <Button onClick={() => setYear(year - 1)} aria-label="Vorjahr" className="!px-2.5">‹</Button>
          <Input type="number" min={1900} max={2200} value={year} onChange={(e) => { const v = Number(e.target.value); if (v >= 1900 && v <= 2200) setYear(v); }} className="!w-24 text-center font-semibold" aria-label="Jahr" />
          <Button onClick={() => setYear(year + 1)} aria-label="Folgejahr" className="!px-2.5">›</Button>
          {year !== thisYear && <Button onClick={() => setYear(thisYear)} className="ml-1 text-xs">Heute</Button>}
        </div>
        <Field label="Bundesland" className="min-w-52">
          <Select value={state} onChange={(e) => chooseState(e.target.value)}>
            {Object.entries(cal.states).map(([k, n]) => <option key={k} value={k}>{n}</option>)}
          </Select>
        </Field>
        <div className="ml-auto flex flex-wrap gap-1.5">
          {[thisYear, thisYear + 1, thisYear + 2, thisYear + 5, thisYear + 10].map((y) => (
            <button key={y} type="button" onClick={() => setYear(y)} className={`rounded-full px-2.5 py-1 text-xs font-medium ${y === year ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"}`}>{y}</button>
          ))}
        </div>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={`Feiertage ${year}`} icon="sun" tone="amber" value={String(cal.statutory_count)} hint={`davon ${cal.weekday_count} auf Mo–Fr · ${cal.state_name}`} />
        <Stat label="Arbeitstage (Mo–Fr)" icon="clock" tone="brand" value={num(cal.workdays)} hint="ohne Feiertage und betrieblich freie Tage" />
        <Stat label="Brückentage" icon="plane" tone="green" value={String(cal.bridges.length)} hint={cal.bridges.length ? `bis ${Math.max(...cal.bridges.map((b) => b.days_off))} Tage am Stück frei` : "keine in diesem Jahr"} />
        <Stat label="Nächster freier Tag" icon="alarm" tone="slate" value={upcoming ? dshort(upcoming.date) : "–"} hint={upcoming?.name ?? (year < thisYear ? "Jahr liegt zurück" : "")} />
      </div>

      <Card>
        <CardHeader title={`Kalender ${year}`} description={`${cal.state_name} · Ostersonntag ${dl(cal.easter)}`} />
        <div className="px-5 pb-5">
          <YearCalendar year={year} holidays={maps.holidays} halfDays={maps.half} regional={maps.regional} bridges={cal.bridges.map((b) => b.date)} />
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader title="Alle freien Tage" />
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {cal.items.map((i) => (
              <li key={i.date + i.name} className={`flex flex-wrap items-center gap-3 px-5 py-2.5 text-sm ${i.on_weekend ? "opacity-60" : ""}`}>
                <span className="w-24 shrink-0 font-mono tabular-nums text-slate-700 dark:text-slate-300">{dl(i.date)}</span>
                <span className="w-24 shrink-0 text-slate-500">{i.weekday}</span>
                <span className="min-w-0 flex-1 font-medium text-slate-900 dark:text-slate-100">{i.name}{i.half_day && <span className="font-normal text-slate-500"> · halber Tag</span>}</span>
                <Badge tone={KIND[i.kind].tone}>{KIND[i.kind].label}</Badge>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Brückentage" description="Ein Urlaubstag – mehrere Tage am Stück frei." />
            {cal.bridges.length === 0 ? <p className="px-5 pb-5 text-sm text-slate-500">Keine Brückentage in {year}.</p> : (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {cal.bridges.map((b) => (
                  <li key={b.date} className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm">
                    <span><strong className="font-medium">{dshort(b.date)}</strong> <span className="text-slate-500">frei {dshort(b.free_from)} – {dshort(b.free_to)}</span></span>
                    <Badge tone="blue">{b.days_off} Tage frei</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader title="Arbeitstage je Monat" description="Mo–Fr ohne Feiertage; halbe freie Tage zählen ½." />
            <div className="grid grid-cols-4 gap-2 px-5 pb-5 sm:grid-cols-6">
              {cal.months.map((m) => (
                <div key={m.month} className="rounded-lg bg-slate-50 px-2 py-1.5 text-center dark:bg-slate-800/60">
                  <p className="text-[11px] text-slate-500">{MONTH_SHORT[m.month - 1]}</p>
                  <p className="font-mono text-sm font-semibold tabular-nums">{num(m.workdays)}</p>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader title="Betriebliche freie Tage" description="Gelten für alle Mitarbeiter, zählen nicht als Urlaub und erscheinen in allen Kalendern." />
            <div className="space-y-3 px-5 pb-5">
              {cal.rules.length === 0 && <p className="text-sm text-slate-500">Noch keine – z. B. Heiligabend und Silvester als halbe Tage.</p>}
              {cal.rules.map((r) => (
                <div key={r.id} className="flex items-center gap-3 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{r.name}{r.half_day && <span className="font-normal text-slate-500"> · halber Tag</span>}</p>
                    <p className="text-xs text-slate-500">{r.year ? `nur ${r.year} · ` : ""}{ruleText(r)}</p>
                  </div>
                  {canEdit && <Button className="!px-2 !py-1 text-xs" onClick={() => void remove(r)}>Entfernen</Button>}
                </div>
              ))}
              {canEdit && cal.suggestions.length > 0 && (
                <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
                  <p className="mb-2 text-xs font-medium text-slate-500">Übliche Brauchtumstage – mit einem Klick übernehmen:</p>
                  <div className="flex flex-wrap gap-2">
                    {cal.suggestions.map((s) => (
                      <button key={s.name} type="button" onClick={() => quickAdd(s)} title={ruleText(s)}
                        className="rounded-full border border-dashed border-slate-300 px-3 py-1 text-xs text-slate-600 hover:border-brand-500 hover:text-brand-700 dark:border-slate-700 dark:text-slate-300">
                        + {s.name}{s.half_day ? " (½)" : ""}{s.date ? ` · ${dshort(s.date)}` : ""}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="In eigenen Kalender übernehmen" description="iCal-Datei für Outlook, Google Kalender oder Apple Kalender – inkl. betrieblicher freier Tage." />
            <div className="flex flex-wrap items-end gap-3 px-5 pb-5">
              <Field label="von"><Input type="number" min={1900} max={2200} value={ical.start} onChange={(e) => setIcal({ ...ical, start: Number(e.target.value) })} className="!w-24" /></Field>
              <Field label="bis"><Input type="number" min={1900} max={2200} value={ical.end} onChange={(e) => setIcal({ ...ical, end: Number(e.target.value) })} className="!w-24" /></Field>
              <a className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700"
                href={`/api/holidays/ical?state=${state}&start=${ical.start}&end=${Math.max(ical.start, ical.end)}`}>.ics herunterladen</a>
            </div>
          </Card>
        </div>
      </div>

      <Drawer open={!!add} onClose={() => setAdd(null)} title="Freien Tag hinzufügen" subtitle="Zählt für alle Mitarbeiter nicht als Arbeitstag."
        footer={<><Button onClick={() => setAdd(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="add-holiday">Speichern</Button></>}>
        {add && (
          <form id="add-holiday" onSubmit={save} className="grid gap-4">
            <Field label="Bezeichnung"><Input required maxLength={80} value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} placeholder="z. B. Betriebsausflug, Rosenmontag" /></Field>
            <Field label="Regel">
              <Select value={add.rule} onChange={(e) => setAdd({ ...add, rule: e.target.value as "fixed" | "easter" })}>
                <option value="fixed">festes Datum</option>
                <option value="easter">abhängig von Ostern (wandert jedes Jahr mit)</option>
              </Select>
            </Field>
            {add.rule === "fixed"
              ? <Field label="Datum"><Input type="date" required value={add.date} onChange={(e) => setAdd({ ...add, date: e.target.value })} /></Field>
              : <Field label="Tage zu Ostersonntag" hint={`z. B. −48 = Rosenmontag, −52 = Weiberfastnacht · ${year}: ${dl(shiftIso(cal.easter, Number(add.easter_offset) || 0))}`}>
                  <Input type="number" min={-80} max={80} required value={add.easter_offset} onChange={(e) => setAdd({ ...add, easter_offset: e.target.value })} /></Field>}
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={add.half_day} onChange={(e) => setAdd({ ...add, half_day: e.target.checked })} /> nur halber Tag frei</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={add.once} onChange={(e) => setAdd({ ...add, once: e.target.checked })} /> nur in diesem Jahr (sonst jedes Jahr)</label>
          </form>
        )}
      </Drawer>
    </div>
  );
}

function shiftIso(iso: string, days: number): string {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
