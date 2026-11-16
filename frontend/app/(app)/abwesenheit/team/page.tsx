"use client";

import { Fragment, useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { countDays, dLabel, KIND_BORDER, KIND_STYLE, num, STATUS, type Absence } from "@/components/Absences";
import { useUser } from "@/components/AuthGate";
import { useFeedback } from "@/components/Feedback";
import { hm } from "@/components/TimeClock";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Table, Tabs } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

interface Row { id: string; name: string; state: string; work_days: number[]; remaining: string; total: string; absences: Absence[] }
interface Calendar {
  month: string; first: string; last: string; holidays: Record<string, Record<string, string>>; half_days?: Record<string, Record<string, string>>;
  employees: Row[]; pending: Absence[]; kinds: Record<string, string>;
}
interface ClockRow {
  id: string; employee_id: string; name: string; started_at: string; ended_at: string | null; break_minutes: number;
  worked_minutes: number | null; source: string; note: string | null; corrected_by: string | null; correction_reason: string | null;
}

const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const thisMonth = () => new Date().toISOString().slice(0, 7);
const shift = (m: string, n: number) => {
  const [y = 2000, mo = 1] = m.split("-").map(Number);
  const d = new Date(Date.UTC(y, mo - 1 + n, 1));
  return d.toISOString().slice(0, 7);
};
const monthName = (m: string) => new Date(m + "-15T12:00:00").toLocaleDateString("de-DE", { month: "long", year: "numeric" });
const tm = (iso: string) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
const localInput = (iso: string) => {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

export default function TeamCalendarPage() {
  const me = useUser();
  const canWrite = me?.permissions?.payroll === "write";
  const [tab, setTab] = useState<"calendar" | "clock">("calendar");
  const [month, setMonth] = useState(thisMonth);
  const [cal, setCal] = useState<Calendar | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [add, setAdd] = useState<{ employee_id: string; kind: string; start_date: string; end_date: string; half_start: boolean; half_end: boolean } | null>(null);
  const { toast, prompt } = useFeedback();

  const load = useCallback(() => {
    api<Calendar>("GET", `/api/absences/calendar?month=${month}`).then((c) => { setCal(c); setError(null); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Kalender nicht erreichbar"));
  }, [month]);
  useEffect(load, [load]);

  const days = useMemo(() => {
    if (!cal) return [];
    const out: { iso: string; d: number; wd: number }[] = [];
    const d = new Date(cal.first + "T12:00:00");
    const last = new Date(cal.last + "T12:00:00");
    while (d <= last) { out.push({ iso: d.toISOString().slice(0, 10), d: d.getDate(), wd: (d.getDay() + 6) % 7 }); d.setDate(d.getDate() + 1); }
    return out;
  }, [cal]);

  const decide = async (a: Absence, approve: boolean) => {
    let note: string | null = null;
    if (!approve) {
      note = await prompt({ title: "Antrag ablehnen", message: `${a.name}: ${a.kind_label} ${dLabel(a.start_date)} – ${dLabel(a.end_date)}`,
        confirmLabel: "Ablehnen", tone: "danger", input: { label: "Begründung für die Mitarbeiterin / den Mitarbeiter", minLength: 3 } });
      if (note === null) return;
    }
    api("POST", `/api/absences/${a.id}/decide`, { approve, note })
      .then(() => { toast(approve ? `Genehmigt – ${a.name} wird benachrichtigt.` : "Abgelehnt."); load(); })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  };

  const saveAdd = (e: FormEvent) => {
    e.preventDefault();
    if (!add) return;
    api("POST", "/api/absences", { ...add, end_date: add.end_date || add.start_date })
      .then(() => { toast("Eingetragen."); setAdd(null); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };

  if (error) return <Alert>{error}</Alert>;
  if (!cal) return <PageSkeleton stats={0} rows={6} />;
  const today = new Date().toISOString().slice(0, 10);
  const addEmp = add ? cal.employees.find((e) => e.id === add.employee_id) : null;
  const addHol = addEmp ? cal.holidays[addEmp.state] ?? {} : {};

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Team" title="Team-Kalender" description="Wer ist wann da? Urlaub genehmigen, Krankmeldungen eintragen, Stempelzeiten prüfen."
        actions={canWrite && cal.employees.length > 0 ? <Button variant="primary" onClick={() => setAdd({ employee_id: cal.employees[0]!.id, kind: "sick", start_date: today, end_date: "", half_start: false, half_end: false })}>Abwesenheit eintragen</Button> : undefined} />

      <Tabs value={tab} onChange={setTab} items={[
        { value: "calendar", label: <>Kalender{cal.pending.length > 0 && <span className="ml-1.5 rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold text-amber-950">{cal.pending.length}</span>}</> },
        { value: "clock", label: "Stempelzeiten" },
      ]} />

      {tab === "calendar" && (
        <>
          {cal.pending.length > 0 && (
            <Card>
              <CardHeader title="Offene Anträge" description="Mit der Genehmigung wird der Urlaub vom Konto abgezogen und die Person per E-Mail informiert." />
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {cal.pending.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                    <span className={`size-2.5 shrink-0 rounded-full ${KIND_STYLE[a.kind]?.split(" ")[0] ?? "bg-slate-400"}`} />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-slate-900 dark:text-slate-100">{a.name} · {a.kind_label} · {num(a.days)} Tage</p>
                      <p className="text-xs text-slate-500">{dLabel(a.start_date)} – {dLabel(a.end_date)}{a.note && <> · „{a.note}“</>}
                        {(() => { const r = cal.employees.find((e) => e.id === a.employee_id); return r ? <> · Rest nach Genehmigung {num(Number(r.remaining) - Number(a.days))}</> : null; })()}</p>
                    </div>
                    {canWrite && <div className="flex gap-2">
                      <Button onClick={() => void decide(a, false)}>Ablehnen</Button>
                      <Button variant="primary" onClick={() => void decide(a, true)}>Genehmigen</Button>
                    </div>}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
              <div className="flex items-center gap-1">
                <Button onClick={() => setMonth(shift(month, -1))} aria-label="Vormonat" className="!px-2.5">‹</Button>
                <h2 className="w-40 text-center text-sm font-semibold capitalize">{monthName(month)}</h2>
                <Button onClick={() => setMonth(shift(month, 1))} aria-label="Folgemonat" className="!px-2.5">›</Button>
                {month !== thisMonth() && <Button onClick={() => setMonth(thisMonth())} className="ml-1 text-xs">Heute</Button>}
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                {Object.entries(cal.kinds).map(([k, l]) => <span key={k} className="flex items-center gap-1"><span className={`size-2.5 rounded-sm ${KIND_STYLE[k]?.split(" ")[0]}`} />{l}</span>)}
                <span className="flex items-center gap-1"><span className="size-2.5 rounded-sm border border-dashed border-slate-400" />beantragt</span>
              </div>
            </div>
            {cal.employees.length === 0 ? <div className="p-6"><Empty>Noch keine Mitarbeiter angelegt.</Empty></div> : (
              <div className={`overflow-x-auto transition-opacity ${cal.month !== month ? "opacity-40" : ""}`}>
                <div className="grid min-w-max text-xs" style={{ gridTemplateColumns: `minmax(10rem,13rem) repeat(${days.length}, 2rem) 4.5rem` }}>
                  <div className="sticky left-0 z-10 border-b border-slate-100 bg-white px-3 py-2 font-medium text-slate-500 dark:border-slate-800 dark:bg-slate-900">Mitarbeiter</div>
                  {days.map((d) => {
                    const names = [...new Set(Object.values(cal.holidays).map((h) => h[d.iso]).filter(Boolean))];
                    const halves = [...new Set(Object.values(cal.half_days ?? {}).map((h) => h[d.iso]).filter(Boolean))];
                    return (
                      <div key={d.iso} title={[...names, ...halves.map((n) => `${n} (halber Tag)`)].join(" · ") || undefined}
                        className={`relative border-b border-l border-slate-100 py-1 text-center dark:border-slate-800 ${names.length ? "bg-amber-100 font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300" : d.wd >= 5 ? "bg-slate-50 text-slate-400 dark:bg-slate-950/40" : "text-slate-500"} ${d.iso === today ? "!bg-teal-50 font-semibold !text-teal-700 dark:!bg-teal-950/50 dark:!text-teal-300" : ""}`}>
                        <div className="text-[10px] uppercase">{WD[d.wd]}</div><div>{d.d}</div>
                        {(names.length > 0 || halves.length > 0) && <span className={`absolute left-1/2 top-0.5 size-1.5 -translate-x-1/2 rounded-full ${names.length ? "bg-amber-500" : "bg-amber-300"}`} />}
                      </div>
                    );
                  })}
                  <div className="border-b border-l border-slate-100 px-2 py-2 text-right font-medium text-slate-500 dark:border-slate-800">Rest</div>
                  {cal.employees.map((e) => {
                    const hol = cal.holidays[e.state] ?? {};
                    return (
                      <Fragment key={e.id}>
                        <div className="sticky left-0 z-10 truncate border-b border-slate-100 bg-white px-3 py-2.5 text-sm font-medium text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200">{e.name}</div>
                        {days.map((d) => {
                          const a = e.absences.find((x) => x.start_date <= d.iso && x.end_date >= d.iso);
                          const off = !e.work_days.includes(d.wd);
                          const h = hol[d.iso];
                          const half = a && ((a.half_start && a.start_date === d.iso) || (a.half_end && a.end_date === d.iso));
                          const start = a && (a.start_date === d.iso || d.d === 1);
                          const end = a && (a.end_date === d.iso || d.iso === cal.last);
                          return (
                            <div key={d.iso} title={a ? `${a.kind_label} (${STATUS[a.status].label})${h ? ` · ${h}` : ""}` : h ?? (off ? "kein Arbeitstag" : undefined)}
                              className={`relative border-b border-l border-slate-100 dark:border-slate-800 ${off || h ? "bg-[repeating-linear-gradient(135deg,transparent,transparent_3px,rgb(148_163_184/0.15)_3px,rgb(148_163_184/0.15)_5px)]" : ""} ${d.iso === today ? "bg-teal-50/60 dark:bg-teal-950/30" : ""}`}>
                              {a && !off && !h && (
                                <span className={`absolute inset-y-1.5 ${start ? "left-1 rounded-l-md" : "left-0"} ${end ? "right-1 rounded-r-md" : "right-0"}
                                  ${a.status === "requested" ? `border-2 border-dashed ${KIND_BORDER[a.kind] ?? "border-slate-400"} bg-white/60 dark:bg-slate-900/60` : KIND_STYLE[a.kind]?.split(" ")[0]}
                                  ${half ? "opacity-60" : ""}`} />
                              )}
                              {h && <span className="absolute inset-0.5 grid place-items-center rounded bg-amber-100/80 text-[9px] font-semibold text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">FT</span>}
                              {!h && cal.half_days?.[e.state]?.[d.iso] && !off && <span className="absolute inset-x-0 top-0.5 text-center text-[9px] font-semibold text-amber-600">½</span>}
                            </div>
                          );
                        })}
                        <div className="border-b border-l border-slate-100 px-2 py-2.5 text-right font-mono tabular-nums text-slate-700 dark:border-slate-800 dark:text-slate-300">{num(e.remaining)}</div>
                      </Fragment>
                    );
                  })}
                </div>
              </div>
            )}
          </Card>
          {(() => {
            const list = new Map<string, Set<string>>();
            for (const [st, h] of Object.entries(cal.holidays)) for (const [d, n] of Object.entries(h)) list.set(`${d}|${n}`, (list.get(`${d}|${n}`) ?? new Set()).add(st));
            for (const [st, h] of Object.entries(cal.half_days ?? {})) for (const [d, n] of Object.entries(h)) list.set(`${d}|${n} (½ Tag)`, (list.get(`${d}|${n} (½ Tag)`) ?? new Set()).add(st));
            const states = Object.keys(cal.holidays).length;
            const entries = [...list.entries()].sort(([a], [b]) => a.localeCompare(b));
            return entries.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium text-slate-500">Feiertage im {monthName(month)}:</span>
                {entries.map(([k, sts]) => {
                  const [d, n] = k.split("|");
                  return <span key={k} className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200">
                    {new Date(d + "T12:00:00").toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" })} {n}{states > 1 && sts.size < states ? ` (${[...sts].join(", ")})` : ""}
                  </span>;
                })}
                <a href="/feiertage" className="font-medium text-brand-600 hover:underline dark:text-brand-300">Alle Feiertage →</a>
              </div>
            );
          })()}
          <p className="text-xs text-slate-500">Schraffiert: Wochenende oder kein vertraglicher Arbeitstag; FT = Feiertag oder betrieblich frei (je Bundesland des Mitarbeiters), ½ = halber freier Tag. „Rest“ = Urlaubsanspruch abzüglich genommener und genehmigter Tage.</p>
        </>
      )}

      {tab === "clock" && <ClockTab month={month} setMonth={setMonth} employees={cal.employees} canWrite={canWrite} />}

      <Drawer open={!!add} onClose={() => setAdd(null)} title="Abwesenheit eintragen" subtitle="Von der Personalverwaltung eingetragen = sofort genehmigt."
        footer={<><Button onClick={() => setAdd(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="add-absence">Eintragen</Button></>}>
        {add && (
          <form id="add-absence" onSubmit={saveAdd} className="grid gap-4 sm:grid-cols-2">
            <Field label="Mitarbeiter" className="sm:col-span-2">
              <Select value={add.employee_id} onChange={(e) => setAdd({ ...add, employee_id: e.target.value })}>
                {cal.employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
              </Select>
            </Field>
            <Field label="Art" className="sm:col-span-2">
              <Select value={add.kind} onChange={(e) => setAdd({ ...add, kind: e.target.value })}>
                {Object.entries(cal.kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Von"><Input type="date" required value={add.start_date} onChange={(e) => setAdd({ ...add, start_date: e.target.value })} /></Field>
            <Field label="Bis"><Input type="date" min={add.start_date} value={add.end_date} onChange={(e) => setAdd({ ...add, end_date: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={add.half_start} onChange={(e) => setAdd({ ...add, half_start: e.target.checked })} /> erster Tag halb</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={add.half_end} onChange={(e) => setAdd({ ...add, half_end: e.target.checked })} /> letzter Tag halb</label>
            {addEmp && <p className="text-sm text-slate-600 sm:col-span-2 dark:text-slate-300">
              {num(countDays(add.start_date, add.end_date || add.start_date, addEmp.work_days, addHol, add.half_start, add.half_end, cal.half_days?.[addEmp.state] ?? {}))} Arbeitstage
              {add.kind === "vacation" && <> · Resturlaub {num(addEmp.remaining)}</>}</p>}
          </form>
        )}
      </Drawer>
    </div>
  );
}

function ClockTab({ month, setMonth, employees, canWrite }: { month: string; setMonth: (m: string) => void; employees: Row[]; canWrite: boolean }) {
  const [emp, setEmp] = useState("");
  const [rows, setRows] = useState<ClockRow[] | null>(null);
  const [edit, setEdit] = useState<{ row: ClockRow; start: string; end: string; reason: string } | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => {
    void api<ClockRow[]>("GET", `/api/timeclock?month=${month}${emp ? `&employee_id=${emp}` : ""}`).then(setRows);
  }, [month, emp]);
  useEffect(load, [load]);

  const sums = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows ?? []) m.set(r.name, (m.get(r.name) ?? 0) + (r.worked_minutes ?? 0));
    return [...m.entries()];
  }, [rows]);

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    api("PUT", `/api/timeclock/${edit.row.id}`, { started_at: new Date(edit.start).toISOString(), ended_at: new Date(edit.end).toISOString(), reason: edit.reason })
      .then(() => { toast("Korrigiert – Änderung ist protokolliert."); setEdit(null); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Monat"><Input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></Field>
        <Field label="Mitarbeiter">
          <Select value={emp} onChange={(e) => setEmp(e.target.value)}>
            <option value="">alle</option>
            {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </Select>
        </Field>
      </div>
      {sums.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {sums.map(([n, m]) => <Badge key={n} tone="blue">{n}: {hm(m)}</Badge>)}
        </div>
      )}
      <Card>
        {!rows ? <div className="p-5 text-sm text-slate-500">Lädt …</div> : rows.length === 0 ? <div className="p-5"><Empty>Keine Stempelzeiten in diesem Monat.</Empty></div> : (
          <Table head={<tr><th>Mitarbeiter</th><th>Tag</th><th>Zeit</th><th className="text-right">Pause</th><th className="text-right">Arbeitszeit</th><th>Quelle</th><th /></tr>}>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="font-medium">{r.name}</td>
                <td>{new Date(r.started_at).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", timeZone: "Europe/Berlin" })}</td>
                <td className="whitespace-nowrap">{tm(r.started_at)}–{r.ended_at ? tm(r.ended_at) : <Badge tone="green">läuft</Badge>}</td>
                <td className="text-right font-mono tabular-nums">{r.break_minutes ? `${r.break_minutes} Min.` : "–"}</td>
                <td className="text-right font-mono tabular-nums">{r.worked_minutes !== null ? hm(r.worked_minutes) : "–"}</td>
                <td>{r.corrected_by ? <span title={r.correction_reason ?? ""}><Badge tone="amber">korrigiert</Badge></span> : <span className="text-xs text-slate-500">{r.source}</span>}</td>
                <td className="text-right">{canWrite && <Button className="!px-2 !py-1 text-xs" onClick={() => setEdit({ row: r, start: localInput(r.started_at), end: localInput(r.ended_at ?? new Date().toISOString()), reason: "" })}>Korrigieren</Button>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Drawer open={!!edit} onClose={() => setEdit(null)} title="Stempelzeit korrigieren" subtitle={edit?.row.name}
        footer={<><Button onClick={() => setEdit(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="fix-clock">Speichern</Button></>}>
        {edit && (
          <form id="fix-clock" onSubmit={save} className="grid gap-4">
            <Field label="Beginn"><Input type="datetime-local" required value={edit.start} onChange={(e) => setEdit({ ...edit, start: e.target.value })} /></Field>
            <Field label="Ende"><Input type="datetime-local" required value={edit.end} onChange={(e) => setEdit({ ...edit, end: e.target.value })} /></Field>
            <Field label="Begründung" hint="Pflicht – wird im Änderungsprotokoll gespeichert."><Input required minLength={3} value={edit.reason} onChange={(e) => setEdit({ ...edit, reason: e.target.value })} placeholder="z. B. Ausstempeln vergessen" /></Field>
            <p className="text-xs text-slate-500">Die gesetzliche Pause wird nach dem Speichern neu berechnet.</p>
          </form>
        )}
      </Drawer>
    </div>
  );
}
