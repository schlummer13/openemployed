"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Skeleton, Table, Tabs } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, HttpError } from "@/lib/http";

interface Emp { id: string; first_name: string; last_name: string }
interface Item { id: string; employee_id: string; name: string; kind: string; label: string; quantity: string | null; rate: string | null; amount: string; source: string | null; created_by: string }
interface Garnishment { id: string; employee_id: string; name: string; creditor: string; reference: string; creditor_iban: string | null; kind: string; fixed_amount: string | null; dependents: number; claim_total: string; paid: string; served_on: string; active: boolean; notes: string | null }
interface Report { id: string; employee_id: string; reason: string; reason_label: string; period_from: string; period_to: string | null; sv_wage: string | null; data: Record<string, string | null>; due_date: string; status: string; submitted_at: string | null; reference: string | null }
interface Statement { id: string; insurer: string; data: Record<string, string>; total: string; submit_by: string; due_date: string; status: string }
interface Cert { id: string; employee_id: string; year: number; data: Record<string, unknown> & { employee: { name: string } }; status: string }

const KINDS: Record<string, string> = {
  bonus: "Prämie/Zulage (laufend)", one_time: "Einmalzahlung (Urlaubs-/Weihnachtsgeld, Bonus)", benefit: "Sachbezug / geldwerter Vorteil",
  overtime: "Überstunden (manuell)", tax_free: "Steuerfreie Erstattung (z. B. Auslagen)", advance: "Vorschuss (Abzug vom Netto)", deduction: "Sonstiger Nettoabzug",
};
const dl = (iso: string | null) => (iso ? new Date(iso.slice(0, 10) + "T12:00:00").toLocaleDateString("de-DE") : "–");
const today = () => new Date().toISOString().slice(0, 10);

/** Ergänzungen zum Lohnlauf: Lohnpositionen, Pfändungen, Meldewesen (DEÜV, Beitragsnachweise), Lohnsteuerbescheinigungen, Einstellungen. */
export function PayrollExtras({ employees, period, onChange }: { employees: Emp[]; period: string; onChange?: () => void }) {
  const [tab, setTab] = useState<"items" | "garn" | "reports" | "certs" | "settings">("items");
  return (
    <Card>
      <div className="px-5 pt-4"><Tabs value={tab} onChange={setTab} items={[
        { value: "items", label: "Lohnpositionen" }, { value: "garn", label: "Pfändungen" }, { value: "reports", label: "SV-Meldungen & Beitragsnachweise" },
        { value: "certs", label: "Lohnsteuerbescheinigungen" }, { value: "settings", label: "Einstellungen" },
      ]} /></div>
      <div className="px-5 pb-5">
        {tab === "items" && <Items employees={employees} period={period} onChange={onChange} />}
        {tab === "garn" && <Garnishments employees={employees} />}
        {tab === "reports" && <Reports period={period} />}
        {tab === "certs" && <Certificates />}
        {tab === "settings" && <Settings />}
      </div>
    </Card>
  );
}

function Items({ employees, period, onChange }: { employees: Emp[]; period: string; onChange?: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [form, setForm] = useState({ employee_id: employees[0]?.id ?? "", kind: "one_time", label: "", quantity: "", rate: "", amount: "", voucher: false });
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<Item[]>("GET", `/api/payroll/items?period=${period}`).then(setItems); }, [period]);
  useEffect(load, [load]);
  const add = (e: FormEvent) => {
    e.preventDefault();
    const n = (v: string) => (v.trim() ? v.replace(",", ".") : null);
    api("POST", "/api/payroll/items", { employee_id: form.employee_id, period, kind: form.kind, label: form.label || KINDS[form.kind], quantity: n(form.quantity),
      rate: n(form.rate), amount: n(form.amount), voucher: form.voucher })
      .then(() => { toast("Position erfasst – Lohnlauf neu berechnen."); setForm({ ...form, label: "", quantity: "", rate: "", amount: "" }); load(); onChange?.(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  const remove = (i: Item) => api("DELETE", `/api/payroll/items/${i.id}`).then(() => { load(); onChange?.(); })
    .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  if (!items) return <Skeleton className="h-40" />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500">Zusätzliche Lohnarten für {period}. Dienstwagen (Listenpreismethode), Überstunden-Auszahlungen aus dem Zeitkonto, Abwesenheiten (unbezahlt, Elternzeit, Mutterschutz, Krankengeld nach 6 Wochen) und Pfändungen werden automatisch berücksichtigt.</p>
      <form onSubmit={add} className="grid gap-3 rounded-xl bg-slate-50 p-3 sm:grid-cols-6 dark:bg-white/[0.03]">
        <Field label="Mitarbeiter" className="sm:col-span-2"><Select value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })}>{employees.map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name}</option>)}</Select></Field>
        <Field label="Art" className="sm:col-span-2"><Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        <Field label="Bezeichnung" className="sm:col-span-2"><Input value={form.label} placeholder={KINDS[form.kind]} onChange={(e) => setForm({ ...form, label: e.target.value })} /></Field>
        <Field label="Menge"><Input inputMode="decimal" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></Field>
        <Field label="Satz €"><Input inputMode="decimal" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} /></Field>
        <Field label="oder Betrag €"><Input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
        {form.kind === "benefit" ? <label className="flex items-end gap-2 pb-2 text-sm sm:col-span-2"><input type="checkbox" checked={form.voucher} onChange={(e) => setForm({ ...form, voucher: e.target.checked })} /> Gutschein/Sachbezug bis 50 € (steuerfrei)</label> : <div className="sm:col-span-2" />}
        <div className="flex items-end"><Button variant="primary" type="submit" disabled={!form.employee_id}>Erfassen</Button></div>
      </form>
      {items.length === 0 ? <Empty>Keine Positionen in diesem Monat.</Empty> : (
        <Table head={<tr><th>Mitarbeiter</th><th>Art</th><th>Bezeichnung</th><th className="text-right">Betrag</th><th /></tr>}>
          {items.map((i) => (
            <tr key={i.id}>
              <td>{i.name}</td><td className="text-xs text-slate-500">{KINDS[i.kind] ?? i.kind}</td>
              <td>{i.label}{i.quantity && i.rate ? <span className="text-xs text-slate-500"> · {i.quantity} × {formatEuro(i.rate)}</span> : null}</td>
              <td className="text-right tabular">{["advance", "deduction"].includes(i.kind) ? "−" : ""}{formatEuro(i.amount)}</td>
              <td className="text-right">{i.source ? <Badge tone="slate">automatisch</Badge> : <Button className="!px-2 !py-1 text-xs" onClick={() => void remove(i)}>Entfernen</Button>}</td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

function Garnishments({ employees }: { employees: Emp[] }) {
  const [data, setData] = useState<{ garnishments: Garnishment[]; params: Record<string, string> } | null>(null);
  const [form, setForm] = useState<(Omit<Garnishment, "id" | "name" | "paid"> & { id: string | null }) | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<{ garnishments: Garnishment[]; params: Record<string, string> }>("GET", "/api/payroll/garnishments").then(setData); }, []);
  useEffect(load, [load]);
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    const body = { employee_id: form.employee_id, creditor: form.creditor, reference: form.reference, creditor_iban: form.creditor_iban || null, kind: form.kind,
      fixed_amount: form.kind === "fixed" ? form.fixed_amount : null, dependents: Number(form.dependents), claim_total: form.claim_total, served_on: form.served_on, active: form.active, notes: form.notes || null };
    (form.id ? api("PUT", `/api/payroll/garnishments/${form.id}`, body) : api("POST", "/api/payroll/garnishments", body))
      .then(() => { toast("Gespeichert – wirkt ab dem nächsten Lohnlauf."); setForm(null); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  if (!data) return <Skeleton className="h-40" />;
  const p = data.params;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500">Pfändbar nach § 850c ZPO: Freibetrag {formatEuro(p.base)} + {formatEuro(p.first_dependent)} (1. Unterhaltsberechtigte/r) + je {formatEuro(p.further_dependent)}; ab {formatEuro(p.cap)} voll pfändbar. Rangfolge nach Zustellungsdatum, Beträge gehen per SEPA an den Gläubiger.</p>
        <Button variant="primary" onClick={() => setForm({ id: null, employee_id: employees[0]?.id ?? "", creditor: "", reference: "", creditor_iban: "", kind: "table", fixed_amount: null, dependents: 0, claim_total: "", served_on: today(), active: true, notes: "" })}>+ Pfändung</Button>
      </div>
      {data.garnishments.length === 0 ? <Empty>Keine Pfändungen.</Empty> : (
        <Table head={<tr><th>Mitarbeiter</th><th>Gläubiger / Aktenzeichen</th><th>Art</th><th className="text-right">Forderung</th><th className="text-right">bezahlt</th><th /></tr>}>
          {data.garnishments.map((g) => (
            <tr key={g.id} className={g.active ? "" : "opacity-50"}>
              <td>{g.name}</td><td>{g.creditor}<span className="block text-xs text-slate-500">{g.reference} · zugestellt {dl(g.served_on)}</span></td>
              <td className="text-xs">{g.kind === "table" ? `Tabelle (${g.dependents} Unterh.)` : g.kind === "fixed" ? `fest ${formatEuro(g.fixed_amount)}` : "Abtretung"}</td>
              <td className="text-right tabular">{formatEuro(g.claim_total)}</td><td className="text-right tabular">{formatEuro(g.paid)}</td>
              <td className="text-right"><Button className="!px-2 !py-1 text-xs" onClick={() => setForm({ ...g, creditor_iban: g.creditor_iban ?? "" })}>Bearbeiten</Button></td>
            </tr>
          ))}
        </Table>
      )}
      <Drawer open={!!form} onClose={() => setForm(null)} title="Pfändung / Abtretung" subtitle="Pfändungs- und Überweisungsbeschluss oder Abtretung"
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="garn-form">Speichern</Button></>}>
        {form && (
          <form id="garn-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Mitarbeiter" className="sm:col-span-2"><Select value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })}>{employees.map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name}</option>)}</Select></Field>
            <Field label="Gläubiger"><Input required value={form.creditor} onChange={(e) => setForm({ ...form, creditor: e.target.value })} /></Field>
            <Field label="Aktenzeichen"><Input required value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
            <Field label="IBAN des Gläubigers" className="sm:col-span-2"><Input value={form.creditor_iban ?? ""} onChange={(e) => setForm({ ...form, creditor_iban: e.target.value })} /></Field>
            <Field label="Art"><Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="table">nach Pfändungstabelle (§ 850c)</option><option value="fixed">fester Betrag (z. B. Unterhalt § 850d)</option><option value="assignment">Abtretung (nach Tabelle)</option></Select></Field>
            {form.kind === "fixed" ? <Field label="Betrag je Monat €"><Input required inputMode="decimal" value={form.fixed_amount ?? ""} onChange={(e) => setForm({ ...form, fixed_amount: e.target.value })} /></Field>
              : <Field label="Unterhaltsberechtigte"><Input type="number" min={0} max={10} value={form.dependents} onChange={(e) => setForm({ ...form, dependents: Number(e.target.value) })} /></Field>}
            <Field label="Gesamtforderung €"><Input required inputMode="decimal" value={form.claim_total} onChange={(e) => setForm({ ...form, claim_total: e.target.value })} /></Field>
            <Field label="Zugestellt am" hint="bestimmt die Rangfolge"><Input type="date" required value={form.served_on} onChange={(e) => setForm({ ...form, served_on: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} /> aktiv</label>
            <p className="text-xs text-slate-500 sm:col-span-2">Drittschuldnererklärung (§ 840 ZPO) innerhalb von 2 Wochen nach Zustellung abgeben. Die Hälfte der Überstundenvergütung bleibt unpfändbar (§ 850a ZPO).</p>
          </form>
        )}
      </Drawer>
    </div>
  );
}

function Reports({ period }: { period: string }) {
  const [rep, setRep] = useState<{ reports: Report[]; employer_number: string | null } | null>(null);
  const [st, setSt] = useState<Statement[] | null>(null);
  const { toast, prompt } = useFeedback();
  const load = useCallback(() => {
    void api<{ reports: Report[]; employer_number: string | null }>("GET", "/api/payroll/sv-reports").then(setRep);
    void api<Statement[]>("GET", `/api/payroll/statements?period=${period}`).then(setSt);
  }, [period]);
  useEffect(load, [load]);
  const submit = async (r: Report) => {
    const ref = await prompt({ title: `${r.reason_label} als übermittelt markieren`, message: `${r.data.name} · Grund ${r.reason}`, confirmLabel: "Übermittelt", input: { label: "Referenz / Datensatz-ID (optional)" } });
    if (ref === null) return;
    api("POST", `/api/payroll/sv-reports/${r.id}`, { status: "submitted", reference: ref || null }).then(() => { toast("Erledigt."); load(); });
  };
  if (!rep || !st) return <Skeleton className="h-40" />;
  const now = today();
  return (
    <div className="space-y-5">
      {!rep.employer_number && <Alert kind="warning">Betriebsnummer fehlt – unter „Einstellungen“ eintragen (Pflicht für alle SV-Meldungen).</Alert>}
      <Alert kind="info">Übermittlung über das kostenlose SV-Meldeportal (sv-meldeportal.de) oder euer Lohnbüro. OpenEmployed berechnet Inhalte und Fristen und dokumentiert, was gemeldet ist.</Alert>
      <div>
        <h3 className="mb-2 text-sm font-semibold">DEÜV-Meldungen</h3>
        {rep.reports.length === 0 ? <Empty>Keine Meldungen.</Empty> : (
          <Table head={<tr><th>Mitarbeiter</th><th>Grund</th><th>Zeitraum</th><th className="text-right">SV-Entgelt</th><th>PGR / BGR</th><th>Frist</th><th /></tr>}>
            {rep.reports.map((r) => (
              <tr key={r.id} className={r.status !== "open" ? "opacity-60" : ""}>
                <td>{r.data.name}<span className="block text-xs text-slate-500">SV-Nr. {r.data.ssn || "fehlt"} · {r.data.insurer || "Kasse fehlt"}</span></td>
                <td><Badge tone={r.reason === "10" ? "green" : r.reason === "30" ? "amber" : "slate"}>{r.reason}</Badge> <span className="text-xs">{r.reason_label}</span></td>
                <td className="whitespace-nowrap text-xs">{dl(r.period_from)}{r.period_to ? ` – ${dl(r.period_to)}` : ""}</td>
                <td className="text-right tabular">{r.sv_wage ? `${Number(r.sv_wage).toLocaleString("de-DE")} €` : "–"}</td>
                <td className="font-mono text-xs">{r.data.person_group} / {r.data.contribution_group}</td>
                <td className={`whitespace-nowrap text-xs ${r.status === "open" && r.due_date < now ? "font-semibold text-rose-600" : ""}`}>{dl(r.due_date)}</td>
                <td className="text-right">{r.status === "open" ? <Button className="!px-2 !py-1 text-xs" onClick={() => void submit(r)}>Übermittelt</Button> : <Badge tone="green">übermittelt{r.reference ? ` · ${r.reference}` : ""}</Badge>}</td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Beitragsnachweise {period}</h3>
        {st.length === 0 ? <Empty>Für {period} gibt es noch keinen Lohnlauf.</Empty> : (
          <div className="grid gap-3 lg:grid-cols-2">
            {st.map((s) => (
              <div key={s.id} className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-800">
                <div className="mb-2 flex items-center justify-between gap-2"><span className="font-semibold">{s.insurer}</span>
                  {s.status === "submitted" ? <Badge tone="green">übermittelt</Badge> : <Badge tone={s.submit_by < now ? "red" : "amber"}>übermitteln bis {dl(s.submit_by)}</Badge>}</div>
                <ul className="space-y-0.5">{Object.entries(s.data).map(([k, v]) => <li key={k} className="flex justify-between gap-2 text-xs"><span className="text-slate-500">{k}</span><span className="tabular">{formatEuro(v)}</span></li>)}</ul>
                <div className="mt-2 flex items-center justify-between border-t border-slate-100 pt-2 dark:border-slate-800"><span className="font-semibold tabular">{formatEuro(s.total)}</span>
                  <span className="text-xs text-slate-500">fällig {dl(s.due_date)}</span>
                  {s.status !== "submitted" && <Button className="!px-2 !py-1 text-xs" onClick={() => api("POST", `/api/payroll/statements/${s.id}/submitted`).then(load)}>Übermittelt</Button>}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Certificates() {
  const [year, setYear] = useState(new Date().getFullYear() - (new Date().getMonth() < 2 ? 1 : 0));
  const [certs, setCerts] = useState<Cert[] | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<Cert[]>("GET", `/api/payroll/certificates?year=${year}`).then(setCerts); }, [year]);
  useEffect(load, [load]);
  const build = () => api<unknown[]>("POST", `/api/payroll/certificates/${year}`).then((r) => { toast(`${r.length} Bescheinigung(en) aus den freigegebenen Lohnläufen erstellt.`); load(); })
    .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  if (!certs) return <Skeleton className="h-40" />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Jahr"><Input type="number" className="!w-28" value={year} onChange={(e) => setYear(Number(e.target.value))} /></Field>
        <Button variant="primary" onClick={() => void build()}>Aus Lohnläufen erstellen</Button>
        <p className="text-xs text-slate-500">Elektronische Übermittlung bis 28.02. des Folgejahres (§ 41b EStG) über ELSTER/ERiC bzw. Lohnbüro; Ausdruck an die Mitarbeiter.</p>
      </div>
      {certs.length === 0 ? <Empty>Noch keine Bescheinigungen für {year}.</Empty> : (
        <Table head={<tr><th>Mitarbeiter</th><th>Zeitraum</th><th className="text-right">Brutto (3.)</th><th className="text-right">LSt (4.)</th><th>Status</th><th /></tr>}>
          {certs.map((c) => (
            <tr key={c.id}>
              <td>{c.data.employee.name}</td><td className="text-xs">{String(c.data["1"])}</td>
              <td className="text-right tabular">{formatEuro(String(c.data["3"]))}</td><td className="text-right tabular">{formatEuro(String(c.data["4"]))}</td>
              <td>{c.status === "transmitted" ? <Badge tone="green">übermittelt</Badge> : <Badge tone="amber">Entwurf</Badge>}</td>
              <td className="text-right"><a className="text-xs font-medium text-brand-600 hover:underline" href={`/api/payroll/certificates/${c.id}/pdf`} target="_blank" rel="noreferrer">Ausdruck (PDF)</a></td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

function Settings() {
  const [s, setS] = useState<{ employer_number: string; garnishment_base: string; garnishment_first: string; garnishment_more: string; garnishment_cap: string } | null>(null);
  const [years, setYears] = useState<number[]>([]);
  const { toast } = useFeedback();
  useEffect(() => {
    void api<{ settings: Record<string, string | null>; pap_years: number[] }>("GET", "/api/payroll/settings").then((r) => {
      setYears(r.pap_years);
      setS({ employer_number: r.settings.employer_number ?? "", garnishment_base: r.settings.garnishment_base ?? "", garnishment_first: r.settings.garnishment_first ?? "",
        garnishment_more: r.settings.garnishment_more ?? "", garnishment_cap: r.settings.garnishment_cap ?? "" });
    });
  }, []);
  if (!s) return <Skeleton className="h-40" />;
  const n = (v: string) => (v.trim() ? v.replace(",", ".") : null);
  const save = (e: FormEvent) => {
    e.preventDefault();
    api("PUT", "/api/payroll/settings", { employer_number: s.employer_number || null, garnishment_base: n(s.garnishment_base), garnishment_first: n(s.garnishment_first),
      garnishment_more: n(s.garnishment_more), garnishment_cap: n(s.garnishment_cap) })
      .then(() => toast("Gespeichert.")).catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  return (
    <form onSubmit={save} className="grid max-w-3xl gap-4 sm:grid-cols-2">
      <Field label="Betriebsnummer (8 Ziffern)" hint="von der Bundesagentur für Arbeit – Pflicht für SV-Meldungen"><Input inputMode="numeric" maxLength={8} value={s.employer_number} onChange={(e) => setS({ ...s, employer_number: e.target.value.replace(/\D/g, "") })} /></Field>
      <div className="text-sm text-slate-500 sm:pt-6">Lohnsteuer nach amtlichem Programmablaufplan für: <strong>{years.join(", ")}</strong> (gegen den BMF-Rechner geprüft).</div>
      <p className="text-sm font-medium sm:col-span-2">Pfändungsfreigrenzen (leer = Werte ab 1.7.2025; jährlich zum 1.7. prüfen)</p>
      <Field label="Grundfreibetrag €/Monat"><Input inputMode="decimal" placeholder="1555,00" value={s.garnishment_base} onChange={(e) => setS({ ...s, garnishment_base: e.target.value })} /></Field>
      <Field label="Erhöhung 1. Unterhaltsberechtigte/r"><Input inputMode="decimal" placeholder="585,23" value={s.garnishment_first} onChange={(e) => setS({ ...s, garnishment_first: e.target.value })} /></Field>
      <Field label="Erhöhung je weitere/r"><Input inputMode="decimal" placeholder="326,04" value={s.garnishment_more} onChange={(e) => setS({ ...s, garnishment_more: e.target.value })} /></Field>
      <Field label="Höchstbetrag (darüber voll pfändbar)"><Input inputMode="decimal" placeholder="4766,99" value={s.garnishment_cap} onChange={(e) => setS({ ...s, garnishment_cap: e.target.value })} /></Field>
      <div className="sm:col-span-2"><Button variant="primary" type="submit">Speichern</Button></div>
    </form>
  );
}
