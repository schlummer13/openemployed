"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Avatar, type Person } from "@/components/Avatar";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Stat, Table } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

interface Trip {
  id: string; number: number; shareholder_id: string; person_name: string; person_color: string | null; purpose: string; destination: string;
  start_date: string; start_time: string; end_date: string; end_time: string; km: string; breakfasts: number; lunches: number; dinners: number;
  other_costs: string; other_note: string | null; meals_amount: string; km_amount: string; total: string; status: "draft" | "booked" | "paid"; paid_on: string | null;
}
type Form = Omit<Trip, "id" | "number" | "person_name" | "person_color" | "meals_amount" | "km_amount" | "total" | "status" | "paid_on"> & { id: string | null };
interface Preview { days: number; gross_meals: string; cuts: string; meals_amount: string; km_amount: string; total: string }

const today = () => new Date().toISOString().slice(0, 10);

export default function TravelPage() {
  const [year, setYear] = useState(new Date().getFullYear());
  const [trips, setTrips] = useState<Trip[] | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm, prompt } = useFeedback();
  const load = useCallback(() => { void api<{ trips: Trip[] }>("GET", `/api/travel?year=${year}`).then((r) => setTrips(r.trips)); }, [year]);
  useEffect(load, [load]);
  useEffect(() => { void api<{ shareholders: Person[] }>("GET", "/api/team").then((t) => setPeople(t.shareholders)); }, []);

  const body = (f: Form) => ({ shareholder_id: f.shareholder_id, purpose: f.purpose, destination: f.destination, start_date: f.start_date, start_time: f.start_time.slice(0, 5),
    end_date: f.end_date, end_time: f.end_time.slice(0, 5), km: toApiDecimal(f.km || "0"), breakfasts: Number(f.breakfasts) || 0, lunches: Number(f.lunches) || 0,
    dinners: Number(f.dinners) || 0, other_costs: toApiDecimal(f.other_costs || "0"), other_note: f.other_note || null });
  // Live-Berechnung der Pauschalen
  useEffect(() => {
    if (!form || !form.shareholder_id) { setPreview(null); return; }
    // Für die Vorschau genügen Zeiten, Kilometer und Mahlzeiten – Ziel/Anlass notfalls mit Platzhalter
    const b = { ...body(form), purpose: form.purpose || "–", destination: form.destination || "–" };
    const t = setTimeout(() => { void api<Preview>("POST", "/api/travel/preview", b).then(setPreview).catch(() => setPreview(null)); }, 250);
    return () => clearTimeout(t);
  }, [form]);

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setBusy(true); setError(null);
    (form.id ? api("PUT", `/api/travel/${form.id}`, body(form)) : api("POST", "/api/travel", body(form)))
      .then(() => { toast("Reisekostenabrechnung gespeichert."); setForm(null); load(); })
      .catch((err: unknown) => setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen"))
      .finally(() => setBusy(false));
  };
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try { await fn(); toast(msg); load(); } catch (err) { toast(err instanceof HttpError ? err.message : "Aktion fehlgeschlagen", "error"); }
  };
  const book = async (t: Trip) => {
    if (!(await confirm({ title: `RK-${String(t.number).padStart(4, "0")} buchen?`, confirmLabel: "Buchen",
      message: <>Erstattung an {t.person_name} über <strong>{formatEuro(t.total)}</strong>. Gebucht als Verbindlichkeit; danach keine Änderung mehr.</> }))) return;
    await act(() => api("POST", `/api/travel/${t.id}/book`), "Reisekosten gebucht.");
  };
  const paid = async (t: Trip) => {
    const d = await prompt({ title: "Auszahlung buchen", confirmLabel: "Als ausgezahlt buchen", message: `${formatEuro(t.total)} an ${t.person_name}`, input: { label: "Datum der Überweisung (TT.MM.JJJJ)", placeholder: formatDate(today()) } });
    if (!d) return;
    const m = d.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
    if (!m) { toast("Datum als TT.MM.JJJJ eingeben.", "error"); return; }
    await act(() => api("POST", `/api/travel/${t.id}/paid`, { paid_on: `${m[3]}-${m[2]!.padStart(2, "0")}-${m[1]!.padStart(2, "0")}` }), "Auszahlung gebucht.");
  };
  const remove = async (t: Trip) => {
    if (!(await confirm({ title: "Entwurf löschen?", tone: "danger", confirmLabel: "Löschen", message: `${t.destination} (${formatDate(t.start_date)})` }))) return;
    await act(() => api("DELETE", `/api/travel/${t.id}`), "Entwurf gelöscht.");
  };

  if (!trips) return <PageSkeleton stats={3} />;
  const sum = (k: keyof Trip) => trips.reduce((s, t) => s + Number(t[k]), 0).toFixed(2);
  const STATUS = { draft: ["Entwurf", "slate"], booked: ["gebucht – offen", "amber"], paid: ["ausgezahlt", "green"] } as const;

  return (
    <>
      <PageHeader title="Reisekosten" description="Dienstreisen der Geschäftsführer: Verpflegungspauschalen, Kilometergeld und Nebenkosten – berechnet nach den steuerlichen Pauschalen (Inland)."
        actions={<>
          <Select aria-label="Jahr" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28">{[0, 1, 2].map((d) => new Date().getFullYear() - d).map((y) => <option key={y} value={y}>{y}</option>)}</Select>
          <Button variant="primary" onClick={() => setForm({ id: null, shareholder_id: people[0]?.id ?? "", purpose: "", destination: "", start_date: today(), start_time: "08:00", end_date: today(), end_time: "18:00", km: "", breakfasts: 0, lunches: 0, dinners: 0, other_costs: "", other_note: "" })}>+ Reise</Button>
        </>} />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Stat label={`Erstattungen ${year}`} icon="plane" tone="brand" value={formatEuro(sum("total"))} hint={`${trips.length} Reisen`} />
        <Stat label="davon Verpflegung" icon="receipt" value={formatEuro(sum("meals_amount"))} />
        <Stat label="davon Kilometergeld" icon="trend" value={formatEuro(sum("km_amount"))} />
      </div>
      <Card>
        {trips.length === 0 ? <Empty>Noch keine Reisen in {year}.</Empty> : (
          <Table head={<tr><th>Nr.</th><th>Person</th><th>Reise</th><th>Zeitraum</th><th className="text-right">Erstattung</th><th>Status</th><th /></tr>}>
            {trips.map((t) => (
              <tr key={t.id}>
                <td className="tabular">RK-{String(t.number).padStart(4, "0")}</td>
                <td><span className="flex items-center gap-2"><Avatar person={{ id: t.shareholder_id, name: t.person_name, color: t.person_color, has_photo: false }} size={22} />{t.person_name}</span></td>
                <td>{t.destination}<span className="block text-xs text-slate-500">{t.purpose}</span></td>
                <td className="tabular text-xs">{formatDate(t.start_date)} {t.start_time.slice(0, 5)}<br />{formatDate(t.end_date)} {t.end_time.slice(0, 5)}</td>
                <td className="text-right tabular font-medium">{formatEuro(t.total)}<span className="block text-xs font-normal text-slate-500">VMA {formatEuro(t.meals_amount)} · km {formatEuro(t.km_amount)}</span></td>
                <td><Badge tone={STATUS[t.status][1]}>{STATUS[t.status][0]}</Badge></td>
                <td className="whitespace-nowrap text-right">
                  {t.status === "draft" && <>
                    <Button variant="ghost" className="!h-7 !text-xs" onClick={() => setForm({ ...t, km: t.km === "0.0" ? "" : t.km.replace(".", ","), other_costs: t.other_costs === "0.00" ? "" : t.other_costs.replace(".", ","), other_note: t.other_note ?? "" })}>Bearbeiten</Button>
                    <Button variant="ghost" className="!h-7 !text-xs" onClick={() => void remove(t)}>Löschen</Button>
                    <Button variant="primary" className="!h-7 !text-xs" onClick={() => void book(t)}>Buchen</Button>
                  </>}
                  {t.status === "booked" && <Button className="!h-7 !text-xs" onClick={() => void paid(t)}>Ausgezahlt</Button>}
                  {t.status === "paid" && t.paid_on && <span className="text-xs text-slate-500">am {formatDate(t.paid_on)}</span>}
                </td>
              </tr>
            ))}
          </Table>
        )}
        <p className="mt-3 text-xs text-slate-500">Hotel- und Fahrkartenbelege mit Vorsteuer bitte als Beleg erfassen; hier nur Pauschalen und Nebenkosten ohne Vorsteuer. Auslandsreisen: Pauschalen nach BMF-Ländertabelle – mit dem Steuerberater klären.</p>
      </Card>

      <Drawer open={!!form} onClose={() => { setForm(null); setError(null); }} size="lg" title={form?.id ? "Reise bearbeiten" : "Neue Dienstreise"}
        subtitle="Abwesenheit von der Wohnung bzw. ersten Tätigkeitsstätte"
        footer={<>{preview && <span className="mr-auto text-sm">Erstattung <strong className="tabular">{formatEuro(preview.total)}</strong></span>}<Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="trip-form" variant="primary" disabled={busy}>Speichern</Button></>}>
        {form && (
          <form id="trip-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Person"><Select required value={form.shareholder_id} onChange={(e) => setForm({ ...form, shareholder_id: e.target.value })}>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
            <Field label="Reiseziel"><Input required placeholder="z. B. Berlin" value={form.destination} onChange={(e) => setForm({ ...form, destination: e.target.value })} /></Field>
            <Field label="Anlass" className="sm:col-span-2"><Input required placeholder="z. B. Kundentermin Rheinwerk" value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} /></Field>
            <Field label="Beginn"><div className="flex gap-2"><Input type="date" required value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /><Input type="time" required value={form.start_time.slice(0, 5)} onChange={(e) => setForm({ ...form, start_time: e.target.value })} className="w-32 shrink-0" /></div></Field>
            <Field label="Ende"><div className="flex gap-2"><Input type="date" required value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /><Input type="time" required value={form.end_time.slice(0, 5)} onChange={(e) => setForm({ ...form, end_time: e.target.value })} className="w-32 shrink-0" /></div></Field>
            <Field label="Gefahrene km mit privatem Pkw" hint="0,30 € je km"><Input inputMode="decimal" placeholder="0" value={form.km} onChange={(e) => setForm({ ...form, km: e.target.value })} /></Field>
            <Field label="Nebenkosten ohne Vorsteuer (€)" hint="z. B. Parken, Nahverkehr"><Input inputMode="decimal" placeholder="0,00" value={form.other_costs} onChange={(e) => setForm({ ...form, other_costs: e.target.value })} /></Field>
            <div className="sm:col-span-2">
              <p className="mb-1.5 text-[13px] font-medium">Gestellte Mahlzeiten (z. B. Hotelfrühstück, Kundenessen)</p>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Frühstück"><Input type="number" min={0} value={form.breakfasts} onChange={(e) => setForm({ ...form, breakfasts: Number(e.target.value) })} /></Field>
                <Field label="Mittagessen"><Input type="number" min={0} value={form.lunches} onChange={(e) => setForm({ ...form, lunches: Number(e.target.value) })} /></Field>
                <Field label="Abendessen"><Input type="number" min={0} value={form.dinners} onChange={(e) => setForm({ ...form, dinners: Number(e.target.value) })} /></Field>
              </div>
            </div>
            {preview && (
              <div className="rounded-xl bg-slate-50 p-4 text-sm sm:col-span-2 dark:bg-white/[0.03]">
                <p className="flex justify-between"><span>Verpflegungspauschale ({preview.days} {preview.days === 1 ? "Tag" : "Tage"})</span><span className="tabular">{formatEuro(preview.gross_meals)}</span></p>
                {Number(preview.cuts) > 0 && <p className="flex justify-between text-slate-500"><span>Kürzung für gestellte Mahlzeiten</span><span className="tabular">− {formatEuro(preview.cuts)}</span></p>}
                <p className="flex justify-between"><span>Kilometergeld</span><span className="tabular">{formatEuro(preview.km_amount)}</span></p>
                <p className="mt-2 flex justify-between border-t border-slate-200 pt-2 font-semibold dark:border-white/10"><span>Erstattung</span><span className="tabular">{formatEuro(preview.total)}</span></p>
              </div>
            )}
            {error && <div className="sm:col-span-2"><Alert>{error}</Alert></div>}
          </form>
        )}
      </Drawer>
    </>
  );
}
