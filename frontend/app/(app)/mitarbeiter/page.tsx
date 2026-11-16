"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import { useFeedback } from "@/components/Feedback";
import { PayrollExtras } from "@/components/PayrollExtras";
import { PersonnelOverview } from "@/components/PersonnelOverview";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Stat, Table, Tabs, Textarea } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

type Kind = "regular" | "minijob" | "werkstudent";
interface Employee {
  id: string; number: number; first_name: string; last_name: string; email: string | null; phone: string | null; birth_date: string | null;
  street: string | null; postal_code: string | null; city: string | null; position: string | null; entry_date: string; exit_date: string | null;
  employment_type: Kind; salary_type: "monthly" | "hourly"; gross_month: string | null; hourly_rate: string | null; weekly_hours: string | null;
  salaries_per_year: number; vacation_days: number | null; tax_id: string | null; tax_class: number; child_allowances: string; church_tax: boolean;
  church_denomination: "ev" | "rk" | "other"; state: string; social_security_number: string | null; health_insurance: "gkv" | "pkv";
  health_insurer: string | null; kv_additional_rate: string | null; pkv_premium: string; children_under_25: number; rv_exempt: boolean;
  iban: string | null; account_holder: string | null; portal_enabled: boolean; notes: string | null; hints: string[];
  work_days: number[]; vacation_carryover: string; time_account_start: string | null; overtime_rule: "account" | "payout" | "included";
  overtime_cap_hours: string | null; tax_factor: string | null; tax_allowance_month: string; tax_addition_month: string; activity_key: string | null;
  nationality: string; birth_name: string | null; birth_place: string | null; car_list_price: string | null; car_distance_km: string | null;
  car_kind: "combustion" | "hybrid" | "electric" | null; car_copayment: string;
}
interface Slip {
  id?: string; employee_id: string | null; shareholder_id: string | null; name: string; employment_type: Kind | "shareholder"; hours: string | null;
  gross: string; lst: string; solz: string; church: string; rv_ee: string; av_ee: string; kv_ee: string; pv_ee: string; rv_er: string; av_er: string;
  kv_er: string; pv_er: string; levies: string; flat_tax: string; pkv_subsidy: string; net: string; payout: string; employer_cost: string;
  iban: string | null; details: { notes?: string[] };
}
interface Totals { count: number; gross: string; net: string; payout: string; taxes: string; sv_total: string; sv_ee: string; sv_er: string; employer_cost: string; levies: string }
interface Run {
  id?: string; period: string; status: "none" | "draft" | "approved"; payslips: Slip[]; totals: Totals; preview?: boolean;
  salaries_paid_on?: string | null; taxes_paid_on?: string | null; sv_paid_on?: string | null; approved_by?: string | null; approved_at?: string | null;
}
interface RunRow { period: string; status: string; count: number; gross: string; payout: string; employer_cost: string; salaries_paid_on: string | null; taxes_paid_on: string | null; sv_paid_on: string | null }
interface Lsta { values: { employees: number; lst: string; solz: string; church_ev: string; church_rk: string; total: string } | null; status: { eric_installed: boolean; hersteller_id: boolean; certificate: boolean; test_mode: boolean }; history: { id: string; status: string; test_mode: boolean; created_at: string; error: string | null }[] }

type Form = Omit<Employee, "id" | "number" | "hints"> & { id: string | null };
const KIND: Record<string, [string, "slate" | "green" | "amber" | "blue"]> = {
  regular: ["Angestellt", "blue"], minijob: ["Minijob", "amber"], werkstudent: ["Werkstudent", "green"], shareholder: ["Geschäftsführung", "slate"],
};
const STATES = ["BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH"];
const today = () => new Date().toISOString().slice(0, 10);
const thisMonth = () => new Date().toISOString().slice(0, 7);
const monthLabel = (p: string) => new Date(`${p}-01T00:00:00`).toLocaleDateString("de-DE", { month: "long", year: "numeric" });
const dec = (v: string | null) => (v == null ? "" : v.replace(".", ","));
const opt = (v: string) => (v.trim() === "" ? null : v.trim());

const EMPTY: Form = {
  id: null, first_name: "", last_name: "", email: "", phone: "", birth_date: null, street: "", postal_code: "", city: "", position: "", entry_date: today(),
  exit_date: null, employment_type: "regular", salary_type: "monthly", gross_month: "", hourly_rate: "", weekly_hours: "40", salaries_per_year: 12,
  vacation_days: 28, tax_id: "", tax_class: 1, child_allowances: "0", church_tax: false, church_denomination: "ev", state: "NW",
  social_security_number: "", health_insurance: "gkv", health_insurer: "", kv_additional_rate: "", pkv_premium: "", children_under_25: 0,
  rv_exempt: false, iban: "", account_holder: "", portal_enabled: false, notes: "", work_days: [0, 1, 2, 3, 4], vacation_carryover: "0",
  time_account_start: null, overtime_rule: "account", overtime_cap_hours: "", tax_factor: "", tax_allowance_month: "0", tax_addition_month: "0",
  activity_key: "", nationality: "DE", birth_name: "", birth_place: "", car_list_price: "", car_distance_km: "", car_kind: null, car_copayment: "0",
};

export default function EmployeesPage() {
  const [tab, setTab] = useState<"team" | "lohn" | "personal">("team");
  const [employees, setEmployees] = useState<Employee[] | null>(null);
  const [limits, setLimits] = useState<{ minijob: string; midijob: string; minimum_wage: string } | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();

  const load = useCallback(() => {
    void api<{ employees: Employee[]; limits: typeof limits }>("GET", "/api/payroll/employees").then((r) => { setEmployees(r.employees); setLimits(r.limits); });
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("tab") === "lohn") setTab("lohn");
    if (q.get("tab") === "personal") setTab("personal");
    if (q.get("neu")) setForm({ ...EMPTY });
  }, []);

  const body = (f: Form) => ({
    first_name: f.first_name, last_name: f.last_name, email: opt(f.email ?? ""), phone: opt(f.phone ?? ""), birth_date: f.birth_date || null,
    street: opt(f.street ?? ""), postal_code: opt(f.postal_code ?? ""), city: opt(f.city ?? ""), position: opt(f.position ?? ""),
    entry_date: f.entry_date, exit_date: f.exit_date || null, employment_type: f.employment_type, salary_type: f.salary_type,
    gross_month: f.salary_type === "monthly" ? toApiDecimal(f.gross_month ?? "") || null : null,
    hourly_rate: f.salary_type === "hourly" ? toApiDecimal(f.hourly_rate ?? "") || null : null,
    weekly_hours: opt(toApiDecimal(f.weekly_hours ?? "")), salaries_per_year: Number(f.salaries_per_year),
    vacation_days: f.vacation_days === null || String(f.vacation_days) === "" ? null : Number(f.vacation_days),
    tax_id: opt(f.tax_id ?? ""), tax_class: Number(f.tax_class), child_allowances: toApiDecimal(f.child_allowances || "0"),
    church_tax: f.church_tax, church_denomination: f.church_denomination, state: f.state, social_security_number: opt(f.social_security_number ?? ""),
    health_insurance: f.health_insurance, health_insurer: opt(f.health_insurer ?? ""),
    kv_additional_rate: f.health_insurance === "gkv" ? opt(toApiDecimal(f.kv_additional_rate ?? "")) : null,
    pkv_premium: f.health_insurance === "pkv" ? toApiDecimal(f.pkv_premium || "0") : "0", children_under_25: Number(f.children_under_25) || 0,
    rv_exempt: f.rv_exempt, iban: opt(f.iban ?? ""), account_holder: opt(f.account_holder ?? ""), portal_enabled: f.portal_enabled, notes: opt(f.notes ?? ""),
    work_days: f.work_days?.length ? f.work_days : [0, 1, 2, 3, 4], vacation_carryover: toApiDecimal(f.vacation_carryover || "0") || "0",
    time_account_start: f.time_account_start || null, overtime_rule: f.overtime_rule || "account",
    overtime_cap_hours: opt(toApiDecimal(f.overtime_cap_hours ?? "")),
    tax_factor: f.tax_class === 4 ? opt(toApiDecimal(f.tax_factor ?? "")) : null, tax_allowance_month: toApiDecimal(f.tax_allowance_month || "0") || "0",
    tax_addition_month: toApiDecimal(f.tax_addition_month || "0") || "0", activity_key: opt(f.activity_key ?? ""), nationality: (f.nationality || "DE").toUpperCase(),
    birth_name: opt(f.birth_name ?? ""), birth_place: opt(f.birth_place ?? ""), car_list_price: opt(toApiDecimal(f.car_list_price ?? "")),
    car_distance_km: opt(toApiDecimal(f.car_distance_km ?? "")), car_kind: f.car_list_price ? (f.car_kind ?? "combustion") : null,
    car_copayment: toApiDecimal(f.car_copayment || "0") || "0",
  });

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setBusy(true); setError(null);
    (form.id ? api<{ hints: string[] }>("PUT", `/api/payroll/employees/${form.id}`, body(form)) : api<{ hints: string[] }>("POST", "/api/payroll/employees", body(form)))
      .then((r) => { toast(r.hints.length ? `Gespeichert. Hinweis: ${r.hints[0]}` : "Mitarbeiter gespeichert.", r.hints.length ? "info" : "success"); setForm(null); load(); })
      .catch((err: unknown) => setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen"))
      .finally(() => setBusy(false));
  };
  const archive = async (emp: Form) => {
    if (!emp.id || !(await confirm({ title: `${emp.first_name} ${emp.last_name} archivieren?`, tone: "danger", confirmLabel: "Archivieren",
      message: "Der Mitarbeiter erscheint nicht mehr in neuen Lohnläufen, der Portal-Zugang endet. Bisherige Abrechnungen bleiben unverändert erhalten." }))) return;
    try { await api("DELETE", `/api/payroll/employees/${emp.id}`); toast("Mitarbeiter archiviert."); setForm(null); load(); }
    catch (err) { toast(err instanceof HttpError ? err.message : "Archivieren fehlgeschlagen", "error"); }
  };

  if (!employees || !limits) return <PageSkeleton stats={3} />;
  const monthly = employees.reduce((s, e) => s + (e.salary_type === "monthly" ? Number(e.gross_month) : Number(e.hourly_rate) * Number(e.weekly_hours || 0) * 13 / 3), 0);
  const set = (patch: Partial<Form>) => form && setForm({ ...form, ...patch });

  return (
    <>
      <PageHeader title="Mitarbeiter & Lohn" eyebrow="Team"
        description="Personalstamm mit Steuer- und Sozialversicherungsdaten, monatliche Gehaltsabrechnung, Lohnbuchung, Überweisung und Lohnsteuer-Anmeldung."
        actions={<Button variant="primary" onClick={() => { setTab("team"); setForm({ ...EMPTY }); }}>+ Mitarbeiter</Button>} />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Stat label="Mitarbeiter" icon="users" tone="brand" value={String(employees.length)} hint={`${employees.filter((e) => e.employment_type === "minijob").length} Minijobs · ${employees.filter((e) => e.portal_enabled).length} mit Portal-Zugang`} />
        <Stat label="Bruttolöhne / Monat" icon="wallet" value={formatEuro(monthly)} hint="ohne Geschäftsführung, Stundenlöhne geschätzt" />
        <Stat label="Mindestlohn 2026" icon="scale" value={`${formatEuro(limits.minimum_wage)}/Std.`} hint={`Minijob-Grenze ${formatEuro(limits.minijob)}`} />
      </div>
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[{ value: "team", label: "Mitarbeiter" }, { value: "personal", label: "Personal" }, { value: "lohn", label: "Lohnabrechnung" }]} /></div>

      {tab === "team" ? (
        <Card>
          {employees.length === 0 ? <Empty>Noch keine Mitarbeiter. Gesellschafter-Geschäftsführer werden unter „GF-Gehälter“ gepflegt und erscheinen automatisch im Lohnlauf.</Empty> : (
            <Table head={<tr><th>Nr.</th><th>Name</th><th>Beschäftigung</th><th className="text-right">Vergütung</th><th>Steuer</th><th>Hinweise</th><th /></tr>}>
              {employees.map((e) => (
                <tr key={e.id} className="cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.03]" onClick={() => setForm({
                  ...e, gross_month: dec(e.gross_month), hourly_rate: dec(e.hourly_rate), weekly_hours: dec(e.weekly_hours), child_allowances: dec(e.child_allowances),
                  kv_additional_rate: dec(e.kv_additional_rate), pkv_premium: dec(e.pkv_premium),
                })}>
                  <td className="tabular text-slate-500">{String(e.number).padStart(3, "0")}</td>
                  <td><span className="font-medium">{e.first_name} {e.last_name}</span><span className="block text-xs text-slate-500">{e.position || "–"}{e.portal_enabled ? " · Portal" : ""}</span></td>
                  <td><Badge tone={KIND[e.employment_type]![1]}>{KIND[e.employment_type]![0]}</Badge><span className="block text-xs text-slate-500">seit {formatDate(e.entry_date)}{e.exit_date ? ` · bis ${formatDate(e.exit_date)}` : ""}</span></td>
                  <td className="text-right tabular">{e.salary_type === "monthly" ? <>{formatEuro(e.gross_month)}<span className="block text-xs text-slate-500">monatlich</span></> : <>{formatEuro(e.hourly_rate)}/Std.<span className="block text-xs text-slate-500">{e.weekly_hours ? `${dec(e.weekly_hours)} Std./Woche` : "nach Stunden"}</span></>}</td>
                  <td className="text-xs">StKl {e.tax_class}{Number(e.child_allowances) > 0 ? ` · ${dec(e.child_allowances)} Kfb` : ""}<span className="block text-slate-500">{e.health_insurance === "pkv" ? "privat versichert" : e.health_insurer || "GKV"}</span></td>
                  <td className="text-xs">{e.hints.length ? <span className="text-amber-700 dark:text-amber-400">{e.hints[0]}{e.hints.length > 1 ? ` (+${e.hints.length - 1})` : ""}</span> : <span className="text-emerald-600">vollständig</span>}</td>
                  <td className="text-right"><Link href={`/mitarbeiter/${e.id}`} onClick={(ev) => ev.stopPropagation()}
                    className="whitespace-nowrap text-xs font-medium text-brand-600 hover:underline dark:text-brand-300">Personalakte →</Link></td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      ) : tab === "lohn" ? <PayrollRuns employees={employees} /> : <PersonnelOverview />}

      <Drawer open={!!form} onClose={() => { setForm(null); setError(null); }} size="lg"
        title={form?.id ? `${form.first_name} ${form.last_name}` : "Neuer Mitarbeiter"} subtitle="Stammdaten, Vergütung, Lohnsteuer- und SV-Merkmale"
        footer={<>
          {form?.id && <Button variant="ghost" className="mr-auto" onClick={() => form && void archive(form)}>Archivieren</Button>}
          <Button onClick={() => setForm(null)}>Abbrechen</Button>
          <Button type="submit" form="emp-form" variant="primary" disabled={busy}>Speichern</Button>
        </>}>
        {form && (
          <form id="emp-form" onSubmit={save} className="grid gap-6">
            <Section title="Person">
              <Field label="Vorname"><Input required value={form.first_name} onChange={(e) => set({ first_name: e.target.value })} /></Field>
              <Field label="Nachname"><Input required value={form.last_name} onChange={(e) => set({ last_name: e.target.value })} /></Field>
              <Field label="E-Mail" hint="für Portal-Zugang und Abrechnungen"><Input type="email" value={form.email ?? ""} onChange={(e) => set({ email: e.target.value })} /></Field>
              <Field label="Telefon"><Input value={form.phone ?? ""} onChange={(e) => set({ phone: e.target.value })} /></Field>
              <Field label="Geburtsdatum"><Input type="date" value={form.birth_date ?? ""} onChange={(e) => set({ birth_date: e.target.value || null })} /></Field>
              <Field label="Position"><Input placeholder="z. B. Entwicklerin" value={form.position ?? ""} onChange={(e) => set({ position: e.target.value })} /></Field>
              <Field label="Straße" className="sm:col-span-2"><Input value={form.street ?? ""} onChange={(e) => set({ street: e.target.value })} /></Field>
              <Field label="PLZ"><Input value={form.postal_code ?? ""} onChange={(e) => set({ postal_code: e.target.value })} /></Field>
              <Field label="Ort"><Input value={form.city ?? ""} onChange={(e) => set({ city: e.target.value })} /></Field>
            </Section>

            <Section title="Beschäftigung & Vergütung">
              <Field label="Art"><Select value={form.employment_type} onChange={(e) => set({ employment_type: e.target.value as Kind, ...(e.target.value === "minijob" ? { salary_type: "hourly" as const } : {}) })}>
                <option value="regular">sozialversicherungspflichtig</option><option value="minijob">Minijob (bis {formatEuro(limits.minijob)})</option><option value="werkstudent">Werkstudent/in</option>
              </Select></Field>
              <Field label="Vergütung"><Select value={form.salary_type} onChange={(e) => set({ salary_type: e.target.value as Form["salary_type"] })}>
                <option value="monthly">Festgehalt (monatlich)</option><option value="hourly">Stundenlohn</option>
              </Select></Field>
              {form.salary_type === "monthly"
                ? <Field label="Bruttogehalt / Monat (€)"><Input required inputMode="decimal" value={form.gross_month ?? ""} onChange={(e) => set({ gross_month: e.target.value })} /></Field>
                : <Field label="Stundenlohn (€)" hint={`mind. ${formatEuro(limits.minimum_wage)}`}><Input required inputMode="decimal" value={form.hourly_rate ?? ""} onChange={(e) => set({ hourly_rate: e.target.value })} /></Field>}
              <Field label="Wochenstunden" hint={form.salary_type === "hourly" ? "für die Stundenschätzung" : "für die Mindestlohnprüfung"}><Input inputMode="decimal" value={form.weekly_hours ?? ""} onChange={(e) => set({ weekly_hours: e.target.value })} /></Field>
              <Field label="Gehälter pro Jahr" hint="13/14 mit Urlaubs-/Weihnachtsgeld"><Select value={form.salaries_per_year} onChange={(e) => set({ salaries_per_year: Number(e.target.value) })}>{[12, 13, 14].map((n) => <option key={n} value={n}>{n}</option>)}</Select></Field>
              <Field label="Eintritt"><Input type="date" required value={form.entry_date} onChange={(e) => set({ entry_date: e.target.value })} /></Field>
              <Field label="Austritt" hint="leer = unbefristet"><Input type="date" value={form.exit_date ?? ""} onChange={(e) => set({ exit_date: e.target.value || null })} /></Field>
            </Section>

            <Section title="Arbeitstage & Urlaub">
              <div className="text-sm sm:col-span-2" role="group" aria-label="Arbeitstage">
                <span className="mb-1.5 block text-[13px] font-medium text-slate-700 dark:text-slate-300">Arbeitstage</span>
                <div className="flex flex-wrap gap-1.5">
                  {["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"].map((d, i) => {
                    const on = (form.work_days ?? []).includes(i);
                    return <button key={d} type="button" aria-pressed={on}
                      onClick={() => set({ work_days: on ? (form.work_days ?? []).filter((x) => x !== i) : [...(form.work_days ?? []), i].sort() })}
                      className={`size-10 rounded-lg text-sm font-medium transition ${on ? "bg-brand-600 text-white shadow-sm" : "bg-slate-100 text-slate-500 hover:bg-slate-200 dark:bg-slate-800"}`}>{d}</button>;
                  })}
                </div>
                <span className="mt-1 block text-xs text-slate-500">zählen für Urlaubstage und das Wochensoll der Stempeluhr</span>
              </div>
              <Field label="Urlaubstage / Jahr (gesamt)" hint={`gesetzl. Minimum ${4 * ((form.work_days ?? []).length || 5)} bei ${(form.work_days ?? []).length || 5} Tagen/Woche`}><Input type="number" min={0} max={60} value={form.vacation_days ?? ""} onChange={(e) => set({ vacation_days: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
              <Field label="Resturlaub aus Vorjahr" hint="verfällt am 31.3."><Input inputMode="decimal" value={dec(form.vacation_carryover ?? "0")} onChange={(e) => set({ vacation_carryover: e.target.value })} /></Field>
              <VacationPreview entry={form.entry_date} exit={form.exit_date} days={form.vacation_days} workDays={form.work_days ?? []} />
            </Section>

            <Section title="Zeitkonto & Überstunden">
              <Field label="Überstunden-Regel"><Select value={form.overtime_rule ?? "account"} onChange={(e) => set({ overtime_rule: e.target.value as Form["overtime_rule"] })}>
                <option value="account">Freizeitausgleich über Zeitkonto</option><option value="payout">Auszahlung</option><option value="included">mit Gehalt abgegolten</option>
              </Select></Field>
              <Field label="Grenze Zeitkonto (Std.)" hint="leer = keine; darüber Hinweis"><Input inputMode="decimal" value={dec(form.overtime_cap_hours ?? "")} onChange={(e) => set({ overtime_cap_hours: e.target.value })} /></Field>
              <Field label="Zeitkonto ab" hint="leer = ab erster Stempelung"><Input type="date" value={form.time_account_start ?? ""} onChange={(e) => set({ time_account_start: e.target.value || null })} /></Field>
            </Section>

            <Section title="Weitere ELStAM-Merkmale, SV-Meldedaten, Dienstwagen">
              {form.tax_class === 4 && <Field label="Faktor (Faktorverfahren)" hint="z. B. 0,912 laut ELStAM"><Input inputMode="decimal" value={dec(form.tax_factor ?? "")} onChange={(e) => set({ tax_factor: e.target.value })} /></Field>}
              <Field label="Freibetrag €/Monat" hint="laut ELStAM"><Input inputMode="decimal" value={dec(form.tax_allowance_month ?? "0")} onChange={(e) => set({ tax_allowance_month: e.target.value })} /></Field>
              <Field label="Hinzurechnungsbetrag €/Monat"><Input inputMode="decimal" value={dec(form.tax_addition_month ?? "0")} onChange={(e) => set({ tax_addition_month: e.target.value })} /></Field>
              <Field label="Tätigkeitsschlüssel (9 Ziffern)" hint="für die SV-Meldung (BA-Schlüsselverzeichnis)"><Input inputMode="numeric" maxLength={9} value={form.activity_key ?? ""} onChange={(e) => set({ activity_key: e.target.value.replace(/\D/g, "") })} /></Field>
              <Field label="Staatsangehörigkeit" hint="ISO-Code, z. B. DE"><Input maxLength={3} value={form.nationality ?? "DE"} onChange={(e) => set({ nationality: e.target.value })} /></Field>
              <Field label="Geburtsname" hint="für die Anmeldung ohne SV-Nummer"><Input value={form.birth_name ?? ""} onChange={(e) => set({ birth_name: e.target.value })} /></Field>
              <Field label="Geburtsort"><Input value={form.birth_place ?? ""} onChange={(e) => set({ birth_place: e.target.value })} /></Field>
              <Field label="Dienstwagen: Bruttolistenpreis €" hint="leer = kein Dienstwagen"><Input inputMode="decimal" value={dec(form.car_list_price ?? "")} onChange={(e) => set({ car_list_price: e.target.value })} /></Field>
              {form.car_list_price && <>
                <Field label="Antrieb"><Select value={form.car_kind ?? "combustion"} onChange={(e) => set({ car_kind: e.target.value as Form["car_kind"] })}>
                  <option value="combustion">Verbrenner (1 %)</option><option value="hybrid">Plug-in-Hybrid (0,5 %)</option><option value="electric">Elektro (0,25 % bis 100.000 €)</option></Select></Field>
                <Field label="Entfernung Wohnung–Arbeit (km)" hint="0,03 % je km"><Input inputMode="decimal" value={dec(form.car_distance_km ?? "")} onChange={(e) => set({ car_distance_km: e.target.value })} /></Field>
                <Field label="Zuzahlung Mitarbeiter €/Monat"><Input inputMode="decimal" value={dec(form.car_copayment ?? "0")} onChange={(e) => set({ car_copayment: e.target.value })} /></Field>
              </>}
            </Section>

            <Section title="Lohnsteuer (ELStAM)">
              <Field label="Steuer-ID" hint="11 Ziffern"><Input inputMode="numeric" maxLength={11} value={form.tax_id ?? ""} onChange={(e) => set({ tax_id: e.target.value.replace(/\D/g, "") })} /></Field>
              <Field label="Steuerklasse"><Select value={form.tax_class} onChange={(e) => set({ tax_class: Number(e.target.value) })}>{[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{["I", "II", "III", "IV", "V", "VI"][n - 1]}</option>)}</Select></Field>
              <Field label="Kinderfreibeträge"><Input inputMode="decimal" value={form.child_allowances} onChange={(e) => set({ child_allowances: e.target.value })} /></Field>
              <Field label="Bundesland (Arbeitsort)"><Select value={form.state} onChange={(e) => set({ state: e.target.value })}>{STATES.map((s) => <option key={s}>{s}</option>)}</Select></Field>
              <Field label="Kirchensteuer"><Select value={form.church_tax ? form.church_denomination : "none"} onChange={(e) => set(e.target.value === "none" ? { church_tax: false } : { church_tax: true, church_denomination: e.target.value as Form["church_denomination"] })}>
                <option value="none">keine</option><option value="ev">evangelisch</option><option value="rk">römisch-katholisch</option><option value="other">andere</option>
              </Select></Field>
            </Section>

            <Section title="Sozialversicherung">
              <Field label="SV-Nummer" hint="z. B. 12 150385 M 123"><Input value={form.social_security_number ?? ""} onChange={(e) => set({ social_security_number: e.target.value })} /></Field>
              <Field label="Krankenversicherung"><Select value={form.health_insurance} onChange={(e) => set({ health_insurance: e.target.value as Form["health_insurance"] })}><option value="gkv">gesetzlich</option><option value="pkv">privat</option></Select></Field>
              <Field label={form.health_insurance === "gkv" ? "Krankenkasse" : "Versicherung"}><Input placeholder={form.health_insurance === "gkv" ? "z. B. TK" : ""} value={form.health_insurer ?? ""} onChange={(e) => set({ health_insurer: e.target.value })} /></Field>
              {form.health_insurance === "gkv"
                ? <Field label="Zusatzbeitrag (%)" hint="leer = Durchschnitt 2,9 %"><Input inputMode="decimal" value={form.kv_additional_rate ?? ""} onChange={(e) => set({ kv_additional_rate: e.target.value })} /></Field>
                : <Field label="PKV/PPV-Beitrag / Monat (€)" hint="Basis für den AG-Zuschuss"><Input inputMode="decimal" value={form.pkv_premium} onChange={(e) => set({ pkv_premium: e.target.value })} /></Field>}
              <Field label="Kinder unter 25" hint="Pflegeversicherung"><Input type="number" min={0} max={20} value={form.children_under_25} onChange={(e) => set({ children_under_25: Number(e.target.value) })} /></Field>
              {form.employment_type === "minijob" && (
                <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={form.rv_exempt} onChange={(e) => set({ rv_exempt: e.target.checked })} />von der RV-Pflicht befreit</label>
              )}
            </Section>

            <Section title="Auszahlung & Self-Service">
              <Field label="IBAN"><Input value={form.iban ?? ""} onChange={(e) => set({ iban: e.target.value })} /></Field>
              <Field label="Kontoinhaber" hint="leer = Mitarbeiter"><Input value={form.account_holder ?? ""} onChange={(e) => set({ account_holder: e.target.value })} /></Field>
              <label className="flex items-start gap-2 text-sm sm:col-span-2">
                <input type="checkbox" className="mt-0.5" checked={form.portal_enabled} onChange={(e) => set({ portal_enabled: e.target.checked })} />
                <span>Zugang zum Mitarbeiter-Portal<span className="block text-xs text-slate-500">Anmeldung per E-Mail-Code unter /portal: Abrechnungen als PDF, Adresse und Bankverbindung selbst pflegen.</span></span>
              </label>
              <Field label="Notizen" className="sm:col-span-2"><Textarea rows={2} value={form.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} /></Field>
            </Section>
            {error && <Alert>{error}</Alert>}
          </form>
        )}
      </Drawer>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset>
      <legend className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{title}</legend>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

/* ───────────────────────── Lohnläufe ───────────────────────── */

function PayrollRuns({ employees }: { employees: Employee[] }) {
  const [period, setPeriod] = useState(thisMonth());
  const [run, setRun] = useState<Run | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [hours, setHours] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<Slip | null>(null);
  const [lsta, setLsta] = useState<Lsta | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm, prompt } = useFeedback();
  const hourly = useMemo(() => new Set(employees.filter((e) => e.salary_type === "hourly").map((e) => e.id)), [employees]);

  const load = useCallback(() => {
    void api<Run>("GET", `/api/payroll/runs/${period}`).then((r) => {
      setRun(r);
      setHours(Object.fromEntries(r.payslips.filter((p) => p.employee_id && p.hours != null).map((p) => [p.employee_id!, dec(p.hours)])));
    });
    void api<RunRow[]>("GET", "/api/payroll/runs").then(setRuns);
    void api<Lsta>("GET", `/api/payroll/runs/${period}/lsta`).then(setLsta).catch(() => setLsta(null));
  }, [period]);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    try { await fn(); toast(msg); load(); } catch (err) { toast(err instanceof HttpError ? err.message : "Aktion fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const calc = () => act(() => api("POST", `/api/payroll/runs/${period}`, {
    hours: Object.fromEntries(Object.entries(hours).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, toApiDecimal(v)])),
  }), run?.status === "draft" ? "Lohnlauf neu berechnet." : "Lohnlauf angelegt.");
  const approve = async () => {
    if (!run || !(await confirm({ title: `Lohnlauf ${monthLabel(period)} freigeben?`, confirmLabel: "Freigeben und buchen",
      message: <>Die {run.totals.count} Abrechnungen werden unveränderlich, die Lohnbuchung ({formatEuro(run.totals.employer_cost)} Personalaufwand) wird festgeschrieben und die Abrechnungen erscheinen im Mitarbeiter-Portal.</> }))) return;
    await act(() => api("POST", `/api/payroll/runs/${period}/approve`), "Lohnlauf freigegeben und gebucht.");
  };
  const paid = async (what: "salaries" | "taxes" | "sv", label: string, amount: string) => {
    const d = await prompt({ title: `${label} als bezahlt buchen`, confirmLabel: "Zahlung buchen", message: `${formatEuro(amount)} – Bankabgang zum Zahlungsdatum`,
      input: { label: "Zahlungsdatum (TT.MM.JJJJ)", placeholder: formatDate(today()) } });
    if (!d) return;
    const m = d.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
    if (!m) { toast("Datum als TT.MM.JJJJ eingeben.", "error"); return; }
    await act(() => api("POST", `/api/payroll/runs/${period}/paid`, { what, paid_on: `${m[3]}-${m[2]!.padStart(2, "0")}-${m[1]!.padStart(2, "0")}` }), "Zahlung gebucht.");
  };
  const sepa = async () => {
    const d = await prompt({ title: "SEPA-Überweisungsdatei für die Nettolöhne", confirmLabel: "Datei erzeugen",
      message: "Die Datei im Online-Banking hochladen und freigeben. Danach hier „Als bezahlt buchen“.", input: { label: "Ausführungsdatum (TT.MM.JJJJ)", placeholder: formatDate(today()) } });
    if (!d) return;
    const m = d.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
    if (!m) { toast("Datum als TT.MM.JJJJ eingeben.", "error"); return; }
    setBusy(true);
    try {
      const r = await api<{ id: string }>("POST", "/api/sepa/salaries", { period, execution_date: `${m[3]}-${m[2]!.padStart(2, "0")}-${m[1]!.padStart(2, "0")}` });
      window.location.href = `/api/sepa/files/${r.id}.xml`;
      toast("SEPA-Datei erzeugt.");
    } catch (err) { toast(err instanceof HttpError ? err.message : "Erzeugen fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!(await confirm({ title: "Entwurf verwerfen?", tone: "danger", confirmLabel: "Verwerfen", message: "Die berechneten Abrechnungen werden gelöscht. Nichts wurde gebucht." }))) return;
    await act(() => api("DELETE", `/api/payroll/runs/${period}`), "Entwurf verworfen.");
  };
  const lstaValidate = () => act(() => api("POST", `/api/payroll/runs/${period}/lsta/validate`), "Lohnsteuer-Anmeldung von ERiC geprüft.");
  const lstaSend = async () => {
    const pin = await prompt({ title: "Lohnsteuer-Anmeldung senden", confirmLabel: "An ELSTER senden",
      message: `Zertifikats-PIN eingeben. ${lsta?.status.test_mode ? "Testmodus: das Finanzamt verarbeitet den Fall nicht." : "ECHTVERSAND an das Finanzamt."}`,
      input: { label: "PIN des ELSTER-Zertifikats", minLength: 4, secret: true } });
    if (!pin) return;
    await act(() => api("POST", `/api/payroll/runs/${period}/lsta/send`, { pin }), "Lohnsteuer-Anmeldung übermittelt.");
  };

  if (!run) return <PageSkeleton stats={4} rows={4} />;
  const t = run.totals;
  const locked = run.status === "approved";
  const months = Array.from({ length: 14 }, (_, i) => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + 1 - i); return d.toISOString().slice(0, 7); });
  const STATUS = { none: ["Vorschau – noch nicht angelegt", "slate"], draft: ["Entwurf", "amber"], approved: ["freigegeben & gebucht", "green"] } as const;

  return (
    <div className="grid gap-6">
      <Card className="!p-0">
        <div className="flex flex-wrap items-center gap-3 px-5 py-4 sm:px-6">
          <Select aria-label="Abrechnungsmonat" value={period} onChange={(e) => setPeriod(e.target.value)} className="w-48">
            {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
          </Select>
          <Badge tone={STATUS[run.status][1]}>{STATUS[run.status][0]}</Badge>
          <div className="ml-auto flex flex-wrap gap-2">
            {run.status === "draft" && <Button variant="ghost" onClick={() => void remove()} disabled={busy}>Verwerfen</Button>}
            {!locked && <Button onClick={() => void calc()} disabled={busy || run.payslips.length === 0}>{run.status === "none" ? "Lohnlauf anlegen" : "Neu berechnen"}</Button>}
            {run.status === "draft" && <Button variant="primary" onClick={() => void approve()} disabled={busy}>Freigeben & buchen</Button>}
          </div>
        </div>
        <div className="grid gap-px border-t border-slate-100 bg-slate-100 sm:grid-cols-4 dark:border-white/5 dark:bg-white/5">
          {[["Brutto", t.gross], ["Auszahlung netto", t.payout], ["Lohnsteuer & Zuschläge", t.taxes], ["Personalkosten gesamt", t.employer_cost]].map(([l, v]) => (
            <div key={l} className="bg-white px-5 py-3 sm:px-6 dark:bg-slate-900"><p className="text-xs text-slate-500">{l}</p><p className="text-lg font-semibold tabular">{formatEuro(v)}</p></div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader title="Abrechnungen" description={locked ? `freigegeben ${run.approved_by ? `von ${run.approved_by}` : ""}` : "Stunden bei Stundenlöhnen eintragen, dann neu berechnen. Klick auf eine Zeile zeigt alle Positionen."} />
        {run.payslips.length === 0 ? <Empty>Für {monthLabel(period)} gibt es keine aktiven Mitarbeiter oder Geschäftsführer-Gehälter.</Empty> : (
          <Table head={<tr><th>Name</th><th className="text-right">Stunden</th><th className="text-right">Brutto</th><th className="text-right">Steuern</th><th className="text-right">SV AN</th><th className="text-right">Auszahlung</th><th className="text-right">AG-Kosten</th><th /></tr>}>
            {run.payslips.map((p) => {
              const notes = p.details?.notes ?? [];
              return (
                <tr key={p.id ?? p.name}>
                  <td><button type="button" className="text-left font-medium hover:underline" onClick={() => setDetail(p)}>{p.name}</button>
                    <span className="mt-0.5 flex flex-wrap items-center gap-1"><Badge tone={KIND[p.employment_type]![1]}>{KIND[p.employment_type]![0]}</Badge>
                      {notes.length > 0 && <span className="text-xs text-amber-700 dark:text-amber-400" title={notes.join("\n")}>{notes.length} Hinweis{notes.length > 1 ? "e" : ""}</span>}</span></td>
                  <td className="text-right">{p.employee_id && hourly.has(p.employee_id) ? (
                    locked ? <span className="tabular">{dec(p.hours)}</span>
                      : <Input aria-label={`Stunden ${p.name}`} inputMode="decimal" className="!w-20 text-right" value={hours[p.employee_id] ?? ""} onChange={(e) => setHours({ ...hours, [p.employee_id!]: e.target.value })} />
                  ) : <span className="text-slate-400">–</span>}</td>
                  <td className="text-right tabular">{formatEuro(p.gross)}</td>
                  <td className="text-right tabular">{formatEuro(Number(p.lst) + Number(p.solz) + Number(p.church) + Number(p.flat_tax))}</td>
                  <td className="text-right tabular">{formatEuro(Number(p.rv_ee) + Number(p.av_ee) + Number(p.kv_ee) + Number(p.pv_ee))}</td>
                  <td className="text-right tabular font-medium">{formatEuro(p.payout)}</td>
                  <td className="text-right tabular text-slate-500">{formatEuro(p.employer_cost)}</td>
                  <td className="text-right">{p.id && <a className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-300" href={`/api/payroll/payslips/${p.id}/pdf`} target="_blank" rel="noreferrer">PDF</a>}</td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      {locked && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader title="Zahlungen" description="Nettolöhne an die Mitarbeiter, Lohnsteuer bis zum 10. des Folgemonats, SV-Beiträge bis zum drittletzten Bankarbeitstag." />
            <ul className="divide-y divide-slate-100 text-sm dark:divide-white/5">
              {([["salaries", "Nettolöhne", t.payout, run.salaries_paid_on], ["taxes", "Lohnsteuer, Soli, Kirchensteuer", t.taxes, run.taxes_paid_on],
                ["sv", "Sozialversicherung (Krankenkassen, Minijob-Zentrale)", t.sv_total, run.sv_paid_on]] as const).map(([what, label, amount, on]) => (
                <li key={what} className="flex flex-wrap items-center gap-3 py-3">
                  <span className="min-w-0 flex-1">{label}<span className="block text-xs text-slate-500 tabular">{formatEuro(amount)}</span></span>
                  {what === "salaries" && !on && <Button className="!h-8 !text-xs" disabled={busy} onClick={() => void sepa()}>SEPA-Datei</Button>}
                  {on ? <Badge tone="green">bezahlt {formatDate(on)}</Badge> : <Button className="!h-8 !text-xs" disabled={busy} onClick={() => void paid(what, label, amount)}>Als bezahlt buchen</Button>}
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <CardHeader title="Lohnsteuer-Anmeldung" description={`Monatlich bis zum 10. ${monthLabel(new Date(new Date(`${period}-01`).setMonth(new Date(`${period}-01`).getMonth() + 1)).toISOString().slice(0, 7))}`} />
            {lsta?.values ? (
              <>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-slate-500">Arbeitnehmer (Kz 86)</dt><dd className="text-right tabular">{lsta.values.employees}</dd>
                  <dt className="text-slate-500">Lohnsteuer (Kz 42)</dt><dd className="text-right tabular">{formatEuro(lsta.values.lst)}</dd>
                  <dt className="text-slate-500">Solidaritätszuschlag (Kz 49)</dt><dd className="text-right tabular">{formatEuro(lsta.values.solz)}</dd>
                  <dt className="text-slate-500">Kirchensteuer ev./rk. (Kz 61/62)</dt><dd className="text-right tabular">{formatEuro(lsta.values.church_ev)} / {formatEuro(lsta.values.church_rk)}</dd>
                  <dt className="border-t border-slate-100 pt-1.5 font-medium dark:border-white/5">Zahllast (Kz 83)</dt><dd className="border-t border-slate-100 pt-1.5 text-right font-semibold tabular dark:border-white/5">{formatEuro(lsta.values.total)}</dd>
                </dl>
                <div className="mt-4 flex flex-wrap gap-2">
                  <a className="inline-flex h-8 items-center rounded-lg px-3 text-xs font-medium shadow-[0_0_0_1px_rgb(15_15_25/0.12)] hover:bg-slate-50 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.1)] dark:hover:bg-white/5" href={`/api/payroll/runs/${period}/lsta.xml`}>XML (Test) herunterladen</a>
                  <Button className="!h-8 !text-xs" disabled={busy || !lsta.status.eric_installed} onClick={() => void lstaValidate()}>Mit ERiC prüfen</Button>
                  <Button variant="primary" className="!h-8 !text-xs" disabled={busy || !(lsta.status.eric_installed && lsta.status.hersteller_id && lsta.status.certificate)} onClick={() => void lstaSend()}>An ELSTER senden</Button>
                </div>
                {!(lsta.status.eric_installed && lsta.status.hersteller_id && lsta.status.certificate) && <p className="mt-2 text-xs text-slate-500">Direktversand braucht ERiC, Hersteller-ID und Zertifikat – siehe docs/betrieb.md (ELSTER). Alternativ die Werte in „Mein ELSTER“ übertragen.</p>}
                {lsta.history.length > 0 && <ul className="mt-3 text-xs text-slate-500">{lsta.history.slice(0, 3).map((h) => <li key={h.id}>{formatDate(h.created_at)} · {h.status}{h.test_mode ? " (Test)" : ""}{h.error ? ` – ${h.error}` : ""}</li>)}</ul>}
              </>
            ) : <Empty>Nach der Freigabe verfügbar.</Empty>}
          </Card>
        </div>
      )}

      {runs.length > 0 && (
        <Card>
          <CardHeader title="Bisherige Lohnläufe" />
          <Table head={<tr><th>Monat</th><th>Status</th><th className="text-right">Abrechnungen</th><th className="text-right">Brutto</th><th className="text-right">Personalkosten</th><th>Zahlungen</th></tr>}>
            {runs.map((r) => (
              <tr key={r.period} className="cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.03]" onClick={() => setPeriod(r.period)}>
                <td className="font-medium">{monthLabel(r.period)}</td>
                <td><Badge tone={r.status === "approved" ? "green" : "amber"}>{r.status === "approved" ? "freigegeben" : "Entwurf"}</Badge></td>
                <td className="text-right tabular">{r.count}</td>
                <td className="text-right tabular">{formatEuro(r.gross)}</td>
                <td className="text-right tabular">{formatEuro(r.employer_cost)}</td>
                <td className="text-xs">{r.status !== "approved" ? "–" : [r.salaries_paid_on && "Löhne", r.taxes_paid_on && "LSt", r.sv_paid_on && "SV"].filter(Boolean).join(" · ") || "offen"}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      <PayrollExtras employees={employees} period={period} onChange={load} />
      <p className="text-xs text-slate-500">Lohnsteuer, Solidaritätszuschlag und Kirchensteuer nach dem amtlichen Programmablaufplan 2026 des BMF (gegen den BMF-Rechner geprüft); Sozialversicherung mit Übergangsbereich (§ 20a SGB IV) und anteiliger Jahres-BBG für Einmalzahlungen. Meldungen (DEÜV, Beitragsnachweise, Lohnsteuerbescheinigung) werden vorbereitet und über das SV-Meldeportal/ELSTER bzw. das Lohnbüro übermittelt. Vor dem Produktivstart einen Monat parallel abgleichen.</p>

      <Drawer open={!!detail} onClose={() => setDetail(null)} title={detail?.name ?? ""} subtitle={`Abrechnung ${monthLabel(period)}`}
        footer={detail?.id ? <a className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700" href={`/api/payroll/payslips/${detail.id}/pdf`} target="_blank" rel="noreferrer">PDF öffnen</a> : undefined}>
        {detail && <SlipDetail slip={detail} />}
      </Drawer>
    </div>
  );
}

function SlipDetail({ slip }: { slip: Slip }) {
  const row = (label: string, v: string, minus = false) => Number(v) !== 0 && (
    <p key={label} className="flex justify-between py-1"><span className={minus ? "text-slate-600 dark:text-slate-300" : ""}>{minus ? "– " : ""}{label}</span><span className="tabular">{formatEuro(v)}</span></p>
  );
  return (
    <div className="grid gap-5 text-sm">
      <div>
        <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Arbeitnehmer</p>
        {row("Bruttoentgelt", slip.gross)}
        {row("Lohnsteuer", slip.lst, true)}{row("Solidaritätszuschlag", slip.solz, true)}{row("Kirchensteuer", slip.church, true)}
        {row("Rentenversicherung", slip.rv_ee, true)}{row("Arbeitslosenversicherung", slip.av_ee, true)}{row("Krankenversicherung", slip.kv_ee, true)}{row("Pflegeversicherung", slip.pv_ee, true)}
        <p className="mt-1 flex justify-between border-t border-slate-200 pt-2 font-semibold dark:border-white/10"><span>Netto</span><span className="tabular">{formatEuro(slip.net)}</span></p>
        {Number(slip.pkv_subsidy) > 0 && row("+ Zuschuss private KV/PV", slip.pkv_subsidy)}
        <p className="flex justify-between font-semibold"><span>Auszahlung</span><span className="tabular">{formatEuro(slip.payout)}</span></p>
      </div>
      <div>
        <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Arbeitgeber</p>
        {row("Rentenversicherung", slip.rv_er)}{row("Arbeitslosenversicherung", slip.av_er)}{row("Krankenversicherung", slip.kv_er)}{row("Pflegeversicherung", slip.pv_er)}
        {row("Umlagen U1/U2/Insolvenzgeld", slip.levies)}{row("Pauschalsteuer", slip.flat_tax)}{row("Zuschuss private KV/PV", slip.pkv_subsidy)}
        <p className="mt-1 flex justify-between border-t border-slate-200 pt-2 font-semibold dark:border-white/10"><span>Gesamtkosten</span><span className="tabular">{formatEuro(slip.employer_cost)}</span></p>
      </div>
      {(slip.details?.notes ?? []).length > 0 && <Alert kind="warning"><ul className="list-disc pl-4">{slip.details.notes!.map((n) => <li key={n}>{n}</li>)}</ul></Alert>}
      <p className="text-xs text-slate-500">Überweisung an {slip.iban ? `${slip.iban.slice(0, 4)} •••• ${slip.iban.slice(-4)}` : "– (IBAN fehlt)"}</p>
    </div>
  );
}

interface PreviewYear { year: number; days: string; rule: string; months: number; statutory_minimum?: string }

/** Urlaubsrechner: zeigt live den Anspruch je Jahr ab Eintritt (BUrlG: Eintritt nach 30.6. anteilig, Austritt, Rundung). */
function VacationPreview({ entry, exit, days, workDays }: { entry: string; exit: string | null; days: number | null; workDays: number[] }) {
  const [years, setYears] = useState<PreviewYear[] | null>(null);
  useEffect(() => {
    if (!entry || days === null || !workDays.length) { setYears(null); return; }
    const t = setTimeout(() => {
      api<{ years: PreviewYear[] }>("POST", "/api/absences/preview", { entry_date: entry, exit_date: exit || null, vacation_days: days, work_days: workDays })
        .then((r) => setYears(r.years)).catch(() => setYears(null));
    }, 300);
    return () => clearTimeout(t);
  }, [entry, exit, days, workDays]);
  if (!years) return null;
  const fmt = (v: string) => Number(v).toLocaleString("de-DE", { maximumFractionDigits: 1 });
  return (
    <div className="rounded-xl border border-brand-100 bg-brand-50/60 p-3 sm:col-span-2 dark:border-brand-900/50 dark:bg-brand-950/30">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-brand-700 dark:text-brand-300">Urlaubsanspruch berechnet</p>
      <ul className="space-y-1.5 text-sm">
        {years.map((y) => (
          <li key={y.year} className="flex items-baseline gap-3">
            <span className="w-12 shrink-0 font-medium text-slate-700 dark:text-slate-300">{y.year}</span>
            <span className="w-16 shrink-0 font-mono font-semibold tabular-nums text-slate-900 dark:text-white">{fmt(y.days)} T.</span>
            <span className="min-w-0 text-xs text-slate-500">{y.rule}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
