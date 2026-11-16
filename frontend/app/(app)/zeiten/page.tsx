"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Avatar, type Person } from "@/components/Avatar";
import { useFeedback } from "@/components/Feedback";
import { Badge, Button, Card, CardHeader, Empty, Field, Input, PageHeader, PageSkeleton, Select, Stat, Table } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

interface Entry {
  id: string; project_id: string; project_name: string; project_color: string; hourly_rate: string | null; customer_id: string | null;
  task_id: string | null; task_title: string | null; shareholder_id: string | null; person_name: string | null; person_color: string | null;
  work_date: string; minutes: number; description: string; billable: boolean; invoice_id: string | null; invoice_number: string | null; invoice_status: string | null;
}
interface Project { id: string; name: string; color: string; customer_id: string | null; hourly_rate: string | null }
interface Customer { id: string; display_name: string }

const hm = (min: number) => `${Math.floor(min / 60)}:${String(min % 60).padStart(2, "0")} h`;
function parseDuration(v: string): number | null {
  const s = v.trim().replace(",", ".");
  const m = s.match(/^(\d{1,2}):([0-5]\d)$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  const n = Number(s);
  return Number.isFinite(n) && n > 0 ? Math.round(n * 60) : null;
}
const monthStart = () => new Date().toISOString().slice(0, 8) + "01";

export default function TimePage() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [filter, setFilter] = useState({ project: "", from: monthStart(), to: "" });
  const [form, setForm] = useState({ project_id: "", shareholder_id: "", work_date: new Date().toISOString().slice(0, 10), duration: "", description: "", billable: true });
  const [busy, setBusy] = useState(false);
  const { toast, confirm, prompt } = useFeedback();

  const load = useCallback(() => {
    const q = new URLSearchParams({ ...(filter.project ? { project_id: filter.project } : {}), ...(filter.from ? { start: filter.from } : {}), ...(filter.to ? { end: filter.to } : {}) });
    void api<{ entries: Entry[] }>("GET", `/api/time?${q}`).then((r) => setEntries(r.entries));
  }, [filter]);
  useEffect(load, [load]);
  useEffect(() => {
    void api<Project[]>("GET", "/api/projects").then((p) => { setProjects(p); setForm((f) => ({ ...f, project_id: f.project_id || p[0]?.id || "" })); });
    void api<{ shareholders: Person[] }>("GET", "/api/team").then((t) => setPeople(t.shareholders));
    void api<Customer[]>("GET", "/api/customers").then(setCustomers);
  }, []);

  const add = (e: FormEvent) => {
    e.preventDefault();
    const minutes = parseDuration(form.duration);
    if (!minutes) { toast("Dauer als 1:30 oder 1,5 eingeben.", "error"); return; }
    setBusy(true);
    api("POST", "/api/time", { project_id: form.project_id, shareholder_id: form.shareholder_id || null, work_date: form.work_date, minutes, description: form.description, billable: form.billable })
      .then(() => { toast(`${hm(minutes)} erfasst.`); setForm({ ...form, duration: "", description: "" }); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen", "error"))
      .finally(() => setBusy(false));
  };
  const remove = async (en: Entry) => {
    if (!(await confirm({ title: "Zeit löschen?", tone: "danger", confirmLabel: "Löschen", message: `${hm(en.minutes)} – ${en.description}` }))) return;
    try { await api("DELETE", `/api/time/${en.id}`); load(); } catch (err) { toast(err instanceof HttpError ? err.message : "Löschen fehlgeschlagen", "error"); }
  };

  const byProject = useMemo(() => {
    const map = new Map<string, { project: Project | undefined; entries: Entry[]; open: Entry[] }>();
    for (const en of entries ?? []) {
      const g = map.get(en.project_id) ?? { project: projects.find((p) => p.id === en.project_id), entries: [], open: [] };
      g.entries.push(en);
      if (en.billable && !en.invoice_id) g.open.push(en);
      map.set(en.project_id, g);
    }
    return [...map.values()];
  }, [entries, projects]);

  const setupBilling = async (p: Project) => {
    const rate = await prompt({ title: `Stundensatz für „${p.name}“`, confirmLabel: "Übernehmen",
      message: "Wird für die Abrechnung dieses Projekts verwendet (netto, zzgl. USt).", input: { label: "Stundensatz in €", placeholder: "95,00" } });
    if (!rate) return null;
    const customer = p.customer_id ?? customers[0]?.id ?? null;
    await api("PUT", `/api/projects/${p.id}/billing`, { hourly_rate: toApiDecimal(rate), customer_id: customer });
    const updated = await api<Project[]>("GET", "/api/projects");
    setProjects(updated);
    return updated.find((x) => x.id === p.id) ?? null;
  };
  const bill = async (g: { project: Project | undefined; open: Entry[] }) => {
    let p = g.project;
    if (!p) return;
    try {
      if (!p.hourly_rate || !p.customer_id) { p = (await setupBilling(p)) ?? undefined; if (!p?.hourly_rate) return; }
      if (!p.customer_id) { toast("Dem Projekt ist kein Kunde zugeordnet – unter Projekte festlegen.", "error"); return; }
      const minutes = g.open.reduce((s, en) => s + en.minutes, 0);
      const customer = customers.find((c) => c.id === p!.customer_id)?.display_name ?? "Kunde";
      if (!(await confirm({ title: `${hm(minutes)} abrechnen?`, confirmLabel: "Rechnungsentwurf erstellen",
        message: <>Rechnungsentwurf an <strong>{customer}</strong> über {g.open.length} Positionen à {formatEuro(p.hourly_rate!)}/h, zusammen ca. <strong>{formatEuro((minutes / 60 * Number(p.hourly_rate)).toFixed(2))}</strong> netto. Der Entwurf kann vor der Freigabe noch bearbeitet werden.</> }))) return;
      const r = await api<{ invoice_id: string }>("POST", `/api/projects/${p.id}/bill`, { entry_ids: g.open.map((en) => en.id) });
      toast("Rechnungsentwurf erstellt."); load();
      window.location.href = `/rechnungen/${r.invoice_id}`;
    } catch (err) { toast(err instanceof HttpError ? err.message : "Abrechnung fehlgeschlagen", "error"); }
  };

  if (!entries) return <PageSkeleton />;
  const total = entries.reduce((s, en) => s + en.minutes, 0);
  const open = entries.filter((en) => en.billable && !en.invoice_id);
  const openValue = open.reduce((s, en) => s + en.minutes / 60 * Number(en.hourly_rate ?? 0), 0);

  return (
    <>
      <PageHeader title="Zeiterfassung" description="Stunden je Projekt erfassen und offene Zeiten mit einem Klick als Rechnungsentwurf an den Projektkunden abrechnen." />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Stat label="Erfasst im Zeitraum" icon="clock" tone="brand" value={hm(total)} hint={`${entries.length} Einträge`} />
        <Stat label="Offen abrechenbar" icon="file" tone="amber" value={hm(open.reduce((s, en) => s + en.minutes, 0))} hint={openValue ? `ca. ${formatEuro(openValue.toFixed(2))} netto` : "Stundensatz im Projekt festlegen"} />
        <Stat label="Nicht abrechenbar" icon="x" value={hm(entries.filter((en) => !en.billable).reduce((s, en) => s + en.minutes, 0))} />
      </div>

      <Card className="mb-6">
        <CardHeader title="Zeit erfassen" />
        <form onSubmit={add} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1.2fr_1fr_0.9fr_0.7fr_2fr_auto] lg:items-end">
          <Field label="Projekt"><Select required value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
          <Field label="Person"><Select value={form.shareholder_id} onChange={(e) => setForm({ ...form, shareholder_id: e.target.value })}><option value="">–</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
          <Field label="Datum"><Input type="date" required value={form.work_date} onChange={(e) => setForm({ ...form, work_date: e.target.value })} /></Field>
          <Field label="Dauer"><Input required placeholder="1:30" value={form.duration} onChange={(e) => setForm({ ...form, duration: e.target.value })} /></Field>
          <Field label="Tätigkeit"><Input required placeholder="z. B. Workshop Anforderungen" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <div className="flex items-center gap-3 sm:col-span-2 lg:col-span-1">
            <label className="flex items-center gap-1.5 whitespace-nowrap text-sm"><input type="checkbox" checked={form.billable} onChange={(e) => setForm({ ...form, billable: e.target.checked })} />abrechenbar</label>
            <Button type="submit" variant="primary" disabled={busy || !projects.length}>Erfassen</Button>
          </div>
        </form>
        {!projects.length && <p className="mt-2 text-sm text-slate-500">Lege zuerst unter <Link href="/projekte" className="underline">Projekte</Link> ein Projekt an.</p>}
      </Card>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Projekt"><Select value={filter.project} onChange={(e) => setFilter({ ...filter, project: e.target.value })} className="w-56"><option value="">alle Projekte</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
        <Field label="Von"><Input type="date" value={filter.from} onChange={(e) => setFilter({ ...filter, from: e.target.value })} className="w-40" /></Field>
        <Field label="Bis"><Input type="date" value={filter.to} onChange={(e) => setFilter({ ...filter, to: e.target.value })} className="w-40" /></Field>
      </div>

      {byProject.length === 0 ? <Card><Empty>Keine Zeiten im gewählten Zeitraum.</Empty></Card> : (
        <div className="space-y-6">
          {byProject.map((g) => (
            <Card key={g.project?.id ?? g.entries[0]?.project_id}>
              <CardHeader title={<span className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: g.project?.color ?? g.entries[0]?.project_color }} />{g.project?.name ?? g.entries[0]?.project_name}</span>}
                description={`${hm(g.entries.reduce((s, en) => s + en.minutes, 0))} erfasst · ${hm(g.open.reduce((s, en) => s + en.minutes, 0))} offen${g.project?.hourly_rate ? ` · ${formatEuro(g.project.hourly_rate)}/h` : " · kein Stundensatz"}`}
                actions={g.open.length > 0 ? <Button variant="primary" onClick={() => void bill(g)}>Offene Zeiten abrechnen</Button> : undefined} />
              <Table head={<tr><th>Datum</th><th>Person</th><th>Tätigkeit</th><th className="text-right">Dauer</th><th>Status</th><th /></tr>}>
                {g.entries.map((en) => (
                  <tr key={en.id}>
                    <td className="tabular">{formatDate(en.work_date)}</td>
                    <td>{en.shareholder_id ? <span className="flex items-center gap-2"><Avatar person={{ id: en.shareholder_id, name: en.person_name ?? "", color: en.person_color, has_photo: false }} size={22} />{en.person_name}</span> : "–"}</td>
                    <td>{en.description}{en.task_title && <span className="block text-xs text-slate-500">{en.task_title}</span>}</td>
                    <td className="text-right tabular">{hm(en.minutes)}</td>
                    <td>{en.invoice_id ? <Link href={`/rechnungen/${en.invoice_id}`}><Badge tone="green">{en.invoice_number ?? "im Entwurf"}</Badge></Link> : en.billable ? <Badge tone="amber">offen</Badge> : <Badge>intern</Badge>}</td>
                    <td className="text-right">{!en.invoice_id && <Button variant="ghost" className="!h-7 !text-xs" onClick={() => void remove(en)}>Löschen</Button>}</td>
                  </tr>
                ))}
              </Table>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
