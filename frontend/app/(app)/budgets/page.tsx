"use client";

import { useFeedback } from "@/components/Feedback";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Select, Stat } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

interface Area {
  id: string; name: string; description: string | null; monthly_budget: string; color: string; categories: string[];
  months: { month: string; budget: string; spent: string }[]; current_month: string; month_spent: string; month_remaining: string;
  month_usage: string | null; ytd_budget: string; ytd_spent: string; ytd_remaining: string; ytd_usage: string | null; over_budget: boolean;
}
interface Overview { year: number; areas: Area[]; totals: { monthly_budget: string; month_spent: string; ytd_budget: string; ytd_spent: string }; unassigned_spent: string }
interface Expense { id: string; receipt_date: string; invoice_number: string | null; description: string; category: string; expense_amount: string; reversed_by: string | null; supplier_name: string; area_id: string | null; area_name: string | null; suggested_area_id: string | null; suggested_area_name: string | null }
interface Meta { categories: { code: string; label: string }[] }

const COLORS = ["#6366f1", "#ef4444", "#f59e0b", "#10b981", "#0ea5e9", "#a855f7", "#ec4899", "#14b8a6"];
const MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
type AreaForm = { id: string | null; name: string; description: string; monthly_budget: string; color: string; categories: string[] };

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : value > 0 ? 100 : 0;
  const over = value > max;
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
      <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: over ? "#ef4444" : color }} />
    </div>
  );
}

export default function BudgetsPage() {
  const fb = useFeedback();
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<Overview | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [form, setForm] = useState<AreaForm | null>(null);
  const [selected, setSelected] = useState<Area | null>(null);
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [filter, setFilter] = useState<"unassigned" | "area" | "all">("unassigned");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void api<Overview>("GET", `/api/budgets?year=${year}`).then((o) => { setData(o); setSelected((s) => (s ? o.areas.find((a) => a.id === s.id) ?? null : null)); });
  }, [year]);
  const loadExpenses = useCallback(() => {
    const q = filter === "unassigned" ? "&unassigned=true" : filter === "area" && selected ? `&area_id=${selected.id}` : "";
    void api<Expense[]>("GET", `/api/budgets/expenses?year=${year}${q}`).then(setExpenses);
    setChecked(new Set());
  }, [year, filter, selected]);
  useEffect(load, [load]);
  useEffect(loadExpenses, [loadExpenses]);
  useEffect(() => { void api<Meta>("GET", "/api/expense-meta").then(setMeta); }, []);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); load(); loadExpenses(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }
  const saveArea = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    void act(async () => {
      const body = { name: form.name, description: form.description || null, monthly_budget: toApiDecimal(form.monthly_budget), color: form.color, categories: form.categories };
      if (form.id) await api("PUT", `/api/budgets/${form.id}`, body); else await api("POST", "/api/budgets", body);
      setNotice(`Bereich „${form.name}“ gespeichert.`);
      setForm(null);
    });
  };
  const deleteArea = (id: string, name: string) => act(async () => {
    if (!await fb.confirm(`Budgetbereich „${name}“ löschen? Die zugeordneten Ausgaben bleiben gebucht und erscheinen wieder unter „Ohne Bereich“.`)) return;
    await api("DELETE", `/api/budgets/${id}`);
    if (selected?.id === id) { setSelected(null); setFilter("unassigned"); }
    setForm(null);
    setNotice(`Bereich „${name}“ gelöscht.`);
  });
  const assign = (areaId: string | null, ids: string[]) => act(async () => {
    const r = await api<{ assigned: number }>("POST", "/api/budgets/assign", { area_id: areaId, receipt_ids: ids });
    setNotice(areaId ? `${r.assigned} Ausgabe(n) zugeordnet.` : `${r.assigned} Zuordnung(en) gelöst.`);
  });
  const catLabel = Object.fromEntries((meta?.categories ?? []).map((c) => [c.code, c.label]));
  const t = data?.totals;
  const suggestions = expenses.filter((x) => x.suggested_area_id).length;

  return (
    <>
      <PageHeader title="Budgets" description="Monatsbudgets je Bereich festlegen und gebuchte Ausgaben zuordnen – so seht ihr jederzeit, wo ihr im Plan liegt."
        actions={<>
          <Select aria-label="Jahr" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28">{[0, 1, 2].map((d) => new Date().getFullYear() - d).map((y) => <option key={y} value={y}>{y}</option>)}</Select>
          <Button variant="primary" onClick={() => setForm({ id: null, name: "", description: "", monthly_budget: "", color: COLORS[(data?.areas.length ?? 0) % COLORS.length] ?? "#6366f1", categories: [] })}>+ Bereich</Button>
        </>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {t && (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Budget pro Monat" icon="pie" tone="brand" value={formatEuro(t.monthly_budget)} hint={`${data?.areas.length ?? 0} Bereiche`} />
          <Stat label="Ausgegeben diesen Monat" icon="receipt" tone={Number(t.month_spent) > Number(t.monthly_budget) ? "red" : "amber"} value={formatEuro(t.month_spent)}
            hint={`Rest ${formatEuro((Number(t.monthly_budget) - Number(t.month_spent)).toFixed(2))}`} />
          <Stat label={`Budget ${year} bis heute`} icon="trend" value={formatEuro(t.ytd_budget)} hint={`ausgegeben ${formatEuro(t.ytd_spent)}`} />
          <Stat label="Ohne Bereich" icon="inbox" tone={Number(data?.unassigned_spent ?? 0) > 0 ? "amber" : "green"} value={formatEuro(data?.unassigned_spent ?? "0")} hint="gebuchte Ausgaben ohne Zuordnung" />
        </div>
      )}

      {form && (
        <Card className="mb-6">
          <form onSubmit={saveArea} className="space-y-3">
            <h2 className="font-semibold">{form.id ? "Bereich bearbeiten" : "Neuer Bereich"}</h2>
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="Name"><Input required placeholder="z. B. Marketing" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Budget pro Monat (€)"><Input required inputMode="decimal" placeholder="2.500,00" value={form.monthly_budget} onChange={(e) => setForm({ ...form, monthly_budget: e.target.value })} /></Field>
              <Field label="Farbe"><div className="flex gap-1.5 pt-1.5">{COLORS.map((c) => <button key={c} type="button" aria-label={`Farbe ${c}`} onClick={() => setForm({ ...form, color: c })} className={`size-6 rounded-full ${form.color === c ? "ring-2 ring-slate-400 ring-offset-2 dark:ring-offset-slate-900" : ""}`} style={{ background: c }} />)}</div></Field>
            </div>
            <Field label="Beschreibung (optional)"><Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
            <div>
              <p className="mb-1.5 text-[13px] font-medium">Kategorien für Zuordnungsvorschläge</p>
              <div className="flex flex-wrap gap-1.5">
                {(meta?.categories ?? []).map((c) => {
                  const on = form.categories.includes(c.code);
                  return <button key={c.code} type="button" onClick={() => setForm({ ...form, categories: on ? form.categories.filter((x) => x !== c.code) : [...form.categories, c.code] })}
                    className={`rounded-full border px-2.5 py-1 text-xs ${on ? "border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-200" : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}>{c.label}</button>;
                })}
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex gap-2"><Button type="submit" variant="primary" disabled={busy}>Speichern</Button><Button onClick={() => setForm(null)}>Abbrechen</Button></div>
              {form.id && <Button variant="ghost" className="text-red-600 hover:!bg-red-50 hover:!text-red-700 dark:hover:!bg-red-500/10" disabled={busy} onClick={() => deleteArea(form.id!, form.name)}>Bereich löschen</Button>}
            </div>
          </form>
        </Card>
      )}

      {data && data.areas.length === 0 ? <Card className="mb-6"><Empty>Noch keine Bereiche. Lege z. B. „Marketing“ mit 2.500 € oder „Büro“ mit 1.000 € pro Monat an.</Empty></Card> : (
        <div className="mb-6 grid gap-4 lg:grid-cols-2">
          {(data?.areas ?? []).map((a) => {
            const budget = Number(a.monthly_budget);
            const max = Math.max(budget, ...a.months.map((m) => Number(m.spent)), 1);
            return (
              <Card key={a.id} className={selected?.id === a.id ? "ring-2 ring-brand-500" : ""}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className="size-3 shrink-0 rounded-full" style={{ background: a.color }} />
                    <div className="min-w-0"><h2 className="font-semibold">{a.name}</h2>{a.description && <p className="text-xs text-slate-500">{a.description}</p>}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {a.over_budget ? <Badge tone="red">überschritten</Badge> : <Badge tone="green">im Plan</Badge>}
                    <Button variant="ghost" className="!px-2 !h-7 !py-0 !text-xs" onClick={() => setForm({ id: a.id, name: a.name, description: a.description ?? "", monthly_budget: a.monthly_budget.replace(".", ","), color: a.color, categories: a.categories })}>bearbeiten</Button>
                  </div>
                </div>
                <div className="mt-4">
                  <div className="mb-1.5 flex items-baseline justify-between text-sm">
                    <span className="text-slate-500">{MONTHS[Number(a.current_month.slice(5)) - 1]} {a.current_month.slice(0, 4)}</span>
                    <span className="tabular"><strong>{formatEuro(a.month_spent)}</strong> <span className="text-slate-500">von {formatEuro(a.monthly_budget)}</span></span>
                  </div>
                  <Bar value={Number(a.month_spent)} max={budget} color={a.color} />
                  <p className={`mt-1.5 text-xs ${Number(a.month_remaining) < 0 ? "text-red-600" : "text-slate-500"}`}>
                    {Number(a.month_remaining) < 0 ? `${formatEuro(Math.abs(Number(a.month_remaining)).toFixed(2))} über Budget` : `noch ${formatEuro(a.month_remaining)} verfügbar`}{a.month_usage ? ` · ${a.month_usage.replace(".", ",")} %` : ""}
                  </p>
                </div>
                <div className="mt-4 flex h-16 items-end gap-1">
                  {a.months.map((m) => (
                    <div key={m.month} className="group relative flex h-full flex-1 flex-col justify-end" title={`${MONTHS[Number(m.month.slice(5)) - 1]}: ${formatEuro(m.spent)} von ${formatEuro(m.budget)}`}>
                      <div className="w-full rounded-t" style={{ height: `${(Number(m.spent) / max) * 100}%`, minHeight: Number(m.spent) > 0 ? 2 : 0, background: Number(m.spent) > budget ? "#ef4444" : a.color, opacity: 0.85 }} />
                      <div className="absolute w-full border-t border-dashed border-slate-400/70" style={{ bottom: `${(budget / max) * 100}%` }} />
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
                  <span>{year} bis heute: {formatEuro(a.ytd_spent)} von {formatEuro(a.ytd_budget)}{a.ytd_usage ? ` (${a.ytd_usage.replace(".", ",")} %)` : ""}</span>
                  <button className="font-medium text-brand-700 hover:underline dark:text-brand-300" onClick={() => { setSelected(a); setFilter("area"); }}>Ausgaben ansehen</button>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            <Button variant={filter === "unassigned" ? "primary" : "secondary"} onClick={() => setFilter("unassigned")}>Ohne Bereich</Button>
            {selected && <Button variant={filter === "area" ? "primary" : "secondary"} onClick={() => setFilter("area")}>{selected.name}</Button>}
            <Button variant={filter === "all" ? "primary" : "secondary"} onClick={() => setFilter("all")}>Alle Ausgaben {year}</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {filter === "unassigned" && suggestions > 0 && <Button onClick={() => act(async () => { const r = await api<{ assigned: number }>("POST", `/api/budgets/apply-suggestions?year=${year}`); setNotice(`${r.assigned} Ausgabe(n) nach Kategorie zugeordnet.`); })} disabled={busy}>{suggestions} Vorschläge übernehmen</Button>}
            {checked.size > 0 && (
              <Select aria-label="Ausgewählte zuordnen" value="" onChange={(e) => { if (e.target.value) void assign(e.target.value === "none" ? null : e.target.value, [...checked]); }} className="w-56">
                <option value="">{checked.size} ausgewählt → zuordnen …</option>
                {(data?.areas ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                <option value="none">Zuordnung lösen</option>
              </Select>
            )}
          </div>
        </div>
        {expenses.length === 0 ? <Empty>{filter === "unassigned" ? "Alle gebuchten Ausgaben sind einem Bereich zugeordnet." : "Keine Ausgaben."}</Empty> : (
          <div className="-mx-5 overflow-x-auto px-5">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs font-medium uppercase tracking-wide text-slate-500 dark:border-slate-800">
                <tr><th className="pb-2.5 pr-3"><input type="checkbox" aria-label="Alle auswählen" checked={checked.size === expenses.length} onChange={(e) => setChecked(e.target.checked ? new Set(expenses.map((x) => x.id)) : new Set())} /></th>
                  <th className="pb-2.5 pr-4">Datum</th><th className="pb-2.5 pr-4">Lieferant / Beschreibung</th><th className="pb-2.5 pr-4">Kategorie</th><th className="pb-2.5 pr-4 text-right">Aufwand</th><th className="pb-2.5">Bereich</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {expenses.map((x) => (
                  <tr key={x.id} className={x.reversed_by ? "text-slate-400 line-through" : ""}>
                    <td className="py-2 pr-3"><input type="checkbox" aria-label={`${x.description} auswählen`} checked={checked.has(x.id)} onChange={(e) => { const n = new Set(checked); if (e.target.checked) n.add(x.id); else n.delete(x.id); setChecked(n); }} /></td>
                    <td className="py-2 pr-4 tabular">{formatDate(x.receipt_date)}</td>
                    <td className="py-2 pr-4">{x.supplier_name}<span className="block text-xs text-slate-500">{x.description}{x.invoice_number ? ` · ${x.invoice_number}` : ""}</span></td>
                    <td className="py-2 pr-4 text-xs">{catLabel[x.category] ?? x.category}</td>
                    <td className="py-2 pr-4 text-right tabular">{formatEuro(x.expense_amount)}</td>
                    <td className="py-2">
                      <Select aria-label="Bereich" value={x.area_id ?? ""} onChange={(e) => void assign(e.target.value || null, [x.id])} className="w-44 !h-7 !py-0 text-xs">
                        <option value="">{x.suggested_area_name ? `Vorschlag: ${x.suggested_area_name}` : "– ohne Bereich –"}</option>
                        {(data?.areas ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                      </Select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500">Gezählt wird der betriebliche Aufwand (netto bei Vorsteuerabzug). Stornierte Belege mindern ihren Bereich automatisch. Die Zuordnung ist eine Auswertung und verändert die festgeschriebenen Belege nicht.</p>
      </Card>
    </>
  );
}
