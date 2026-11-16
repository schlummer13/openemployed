"use client";

import { useFeedback } from "@/components/Feedback";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Avatar } from "@/components/Avatar";
import { ShareholderDialog, type HolderData } from "@/components/ShareholderDialog";
import { Alert, Button, Card, Field, Input, PageHeader, Select } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

interface Salary { gross_month: string; lst_month: string; solz_month: string; church_month: string; sv_employee_month: string; net_month: string; sv_employer_month: string; levies_month: string; employer_cost_month: string; employer_cost_year: string }
interface Holder {
  id: string; name: string; email: string | null; share_percent: string; is_managing_director: boolean;
  compensation: { valid_from: string; gross_month: string; salaries_per_year: number; sv_status: string; tax_class: number; church_tax: boolean; childless: boolean } | null;
  upcoming: { valid_from: string; gross_month: string }[];
  planned: boolean;
  salary: Salary | null;
  tax_profile: TaxProfile | null;
}
interface TaxProfile {
  valid_from: string; tax_class: number; child_allowances: string; church_tax: boolean; state: string; birth_year: number | null;
  children_under_25: number; health_insurance: "gkv" | "pkv"; health_insurer: string | null; kv_additional_rate: string | null; pkv_premium: string;
}
const STATES: [string, string][] = [["BW", "Baden-Württemberg"], ["BY", "Bayern"], ["BE", "Berlin"], ["BB", "Brandenburg"], ["HB", "Bremen"], ["HH", "Hamburg"], ["HE", "Hessen"], ["MV", "Mecklenburg-Vorpommern"], ["NI", "Niedersachsen"], ["NW", "Nordrhein-Westfalen"], ["RP", "Rheinland-Pfalz"], ["SL", "Saarland"], ["SN", "Sachsen"], ["ST", "Sachsen-Anhalt"], ["SH", "Schleswig-Holstein"], ["TH", "Thüringen"]];
const ROMAN = ["", "I", "II", "III", "IV", "V", "VI"];
interface Team {
  shareholders: Holder[];
  share_total: string;
  capacity: { mrr: string; avg_expenses: string; buffer_percent: string; budget: string; managing_directors: number; per_md_employer_cost: string; per_md_gross_estimate: string; current_employer_cost: string; usage_percent: string | null; note: string };
  distribution: { distributable: string; ug_reserve: string; rows: { shareholder_id: string; name: string; share_percent: string; gross: string; kapest: string; solz: string; church: string; net: string }[]; note: string };
  params: { year: number };
}

function nextMonthStart(): string {
  const d = new Date();
  const n = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-01`;
}

export default function SalaryPage() {
  const fb = useFeedback();
  const [team, setTeam] = useState<Team | null>(null);
  const [comp, setComp] = useState({ valid_from: nextMonthStart(), gross_month: "", salaries_per_year: 12, sv_status: "pflichtig", tax_class: 1, church_tax: false, childless: true, target: "all" });
  const [error, setError] = useState<string | null>(null);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };
  const [dialog, setDialog] = useState<{ holder: HolderData | null } | null>(null);
  const load = useCallback(() => { void api<Team>("GET", "/api/team").then(setTeam).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen")); }, []);
  useEffect(load, [load]);

  async function act(fn: () => Promise<void>) {
    setError(null); setNotice(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); }
  }

  const addComp = (e: FormEvent) => {
    e.preventDefault();
    void act(async () => {
      const ids = comp.target === "all" ? (team?.shareholders.filter((s) => s.is_managing_director).map((s) => s.id) ?? []) : [comp.target];
      const { target: _t, ...rest } = comp;
      await api("POST", "/api/compensations", { ...rest, shareholder_ids: ids, gross_month: toApiDecimal(comp.gross_month) });
      setNotice(`Gehalt ab ${formatDate(comp.valid_from)} festgelegt.`);
    });
  };

  const cap = team?.capacity;
  return (
    <>
      <PageHeader title="Gehälter & Ausschüttung" description="Gesellschafter-Geschäftsführer mit allen lohnrelevanten Angaben, Gehaltsplanung und Ausschüttung. Alle Werte sind für alle sichtbar (komplett transparent)."
        actions={<Button variant="primary" onClick={() => setDialog({ holder: null })}>+ Gesellschafter</Button>} />
      {dialog && <ShareholderDialog holder={dialog.holder} onClose={() => setDialog(null)} onSaved={(msg) => { setDialog(null); setNotice(msg); load(); }} />}
      <div className="mb-4"><Alert kind="warning">Näherungsrechnung für die Planung (Steuertarif und Sozialversicherung {team?.params.year}, persönliche Steuerklasse, Kinderfreibeträge, Kirchensteuer, Kasse). Verbindlich ist die Lohnabrechnung beim Steuerberater. Statusfeststellung (Sozialversicherungspflicht) vor dem ersten Gehalt klären.</Alert></div>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {cap && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Card><p className="text-xs text-slate-500">Tragbares Budget / Monat</p><p className="mt-1 text-xl font-semibold tabular">{formatEuro(cap.budget)}</p><p className="mt-1 text-xs text-slate-500">MRR {formatEuro(cap.mrr)} − Ø Ausgaben {formatEuro(cap.avg_expenses)} − {cap.buffer_percent.replace(/\.0+$/, "")} % Puffer</p></Card>
          <Card><p className="text-xs text-slate-500">Je Geschäftsführer (AG-Kosten)</p><p className="mt-1 text-xl font-semibold tabular">{formatEuro(cap.per_md_employer_cost)}</p><p className="mt-1 text-xs text-slate-500">≈ {formatEuro(cap.per_md_gross_estimate)} brutto</p></Card>
          <Card><p className="text-xs text-slate-500">AG-Kosten gesamt (aktuell bzw. geplant)</p><p className="mt-1 text-xl font-semibold tabular">{formatEuro(cap.current_employer_cost)}</p></Card>
          <Card><p className="text-xs text-slate-500">Auslastung des Budgets</p><p className={`mt-1 text-xl font-semibold tabular ${cap.usage_percent && Number(cap.usage_percent) > 100 ? "text-red-600" : Number(cap.usage_percent ?? 0) > 80 ? "text-amber-600" : "text-emerald-600"}`}>{cap.usage_percent ? `${cap.usage_percent.replace(".", ",")} %` : "–"}</p></Card>
        </div>
      )}

      <Card className="mb-4">
        <h2 className="mb-3 font-medium">Gesellschafter-Geschäftsführer</h2>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm tabular">
            <thead className="border-b border-slate-200 text-left text-xs text-slate-500 dark:border-slate-800"><tr><th className="pb-2">Name</th><th className="pb-2 text-right">Anteil</th><th className="pb-2 text-right">Brutto</th><th className="pb-2 text-right">Lohnsteuer</th><th className="pb-2 text-right">Soli/KiSt</th><th className="pb-2 text-right">SV (AN)</th><th className="pb-2 text-right">Netto</th><th className="pb-2 text-right">SV (AG) + Umlagen</th><th className="pb-2 text-right">AG-Kosten</th></tr></thead>
            <tbody>
              {team?.shareholders.map((s) => (
                <tr key={s.id} className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2"><div className="flex items-start gap-2.5"><Avatar person={{ id: s.id, name: s.name, color: (s as unknown as HolderData).color, has_photo: (s as unknown as HolderData).has_photo }} size={32} version={(s as unknown as HolderData).updated_at} /><div><span className="font-medium">{s.name}</span><div className="text-xs text-slate-500">{s.compensation ? `${s.planned ? "geplant ab" : "seit"} ${formatDate(s.compensation.valid_from)} · SV ${s.compensation.sv_status}` : "kein Gehalt festgelegt"}{s.upcoming.map((u) => ` · ab ${formatDate(u.valid_from)}: ${formatEuro(u.gross_month)}`).join("")}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
                      {s.tax_profile ? <span className="text-slate-500">StKl. {ROMAN[s.tax_profile.tax_class]} · {s.tax_profile.child_allowances !== "0.0" && s.tax_profile.child_allowances !== "0" ? `${s.tax_profile.child_allowances.replace(".", ",")} Kinderfreibetr. · ` : ""}{s.tax_profile.church_tax ? "KiSt · " : ""}{s.tax_profile.health_insurance === "pkv" ? "PKV" : s.tax_profile.health_insurer ?? "GKV"}</span>
                        : <span className="text-amber-600 dark:text-amber-400">Steuerdaten fehlen</span>}
                      <button type="button" className="font-medium text-brand-700 hover:underline dark:text-brand-300" onClick={() => setDialog({ holder: s as unknown as HolderData })}>bearbeiten</button>
                    </div></div></div></td>
                  <td className="py-2 text-right">{s.share_percent.replace(".", ",")} %</td>
                  {s.salary ? (
                    <>
                      <td className="py-2 text-right">{formatEuro(s.salary.gross_month)}</td>
                      <td className="py-2 text-right">{formatEuro(s.salary.lst_month)}</td>
                      <td className="py-2 text-right">{formatEuro((Number(s.salary.solz_month) + Number(s.salary.church_month)).toFixed(2))}</td>
                      <td className="py-2 text-right">{formatEuro(s.salary.sv_employee_month)}</td>
                      <td className="py-2 text-right font-semibold">{formatEuro(s.salary.net_month)}</td>
                      <td className="py-2 text-right">{formatEuro((Number(s.salary.sv_employer_month) + Number(s.salary.levies_month)).toFixed(2))}</td>
                      <td className="py-2 text-right">{formatEuro(s.salary.employer_cost_month)}</td>
                    </>
                  ) : <td colSpan={7} className="py-2 text-right text-slate-400">–</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-slate-500">Summe Anteile: {team?.share_total.replace(".", ",")} %</p>
      </Card>

      <Card className="mb-4">
        <h2 className="mb-3 font-medium">Gehalt festlegen</h2>
        <form onSubmit={addComp} className="grid gap-3 md:grid-cols-4">
          <Field label="Für"><Select value={comp.target} onChange={(e) => setComp({ ...comp, target: e.target.value })}><option value="all">alle Geschäftsführer (gleiches Gehalt)</option>{team?.shareholders.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>
          <Field label="Gültig ab" hint="nur ab laufendem Monat (keine Rückwirkung)"><Input type="date" required value={comp.valid_from} onChange={(e) => setComp({ ...comp, valid_from: e.target.value })} /></Field>
          <Field label="Brutto / Monat (€)"><Input required inputMode="decimal" value={comp.gross_month} placeholder={cap ? cap.per_md_gross_estimate.replace(".", ",") : ""} onChange={(e) => setComp({ ...comp, gross_month: e.target.value })} /></Field>
          <Field label="Gehälter / Jahr"><Select value={comp.salaries_per_year} onChange={(e) => setComp({ ...comp, salaries_per_year: Number(e.target.value) })}><option value={12}>12</option><option value={13}>13</option><option value={14}>14</option></Select></Field>
          <Field label="Sozialversicherung"><Select value={comp.sv_status} onChange={(e) => setComp({ ...comp, sv_status: e.target.value })}><option value="pflichtig">pflichtig (Regelfall bei 4 × 25 %)</option><option value="frei">frei (nur mit Statusbescheid)</option></Select></Field>
          <p className="self-end pb-2 text-xs text-slate-500 md:col-span-3">Steuerklasse, Kinder, Kirchensteuer und Krankenkasse werden aus den persönlichen Steuerdaten des Geschäftsführers übernommen.</p>
          <div className="md:col-span-4"><Button type="submit" variant="primary" disabled={!team?.shareholders.length}>Gehalt festlegen</Button></div>
        </form>
      </Card>

      {team && (
        <Card>
          <h2 className="mb-1 font-medium">Mögliche Gewinnausschüttung (Schätzung laufendes Jahr)</h2>
          <p className="mb-3 text-sm text-slate-500">Ausschüttbar: {formatEuro(team.distribution.distributable)} (nach Steuern, abzüglich UG-Rücklage {formatEuro(team.distribution.ug_reserve)})</p>
          <div className="overflow-x-auto"><table className="w-full text-sm tabular">
            <thead className="border-b border-slate-200 text-left text-xs text-slate-500 dark:border-slate-800"><tr><th className="pb-2">Gesellschafter</th><th className="pb-2 text-right">Brutto</th><th className="pb-2 text-right">KapESt 25 %</th><th className="pb-2 text-right">Soli</th><th className="pb-2 text-right">KiSt</th><th className="pb-2 text-right">Netto</th></tr></thead>
            <tbody>{team.distribution.rows.map((r) => <tr key={r.shareholder_id} className="border-b border-slate-100 dark:border-slate-800"><td className="py-1.5">{r.name}</td><td className="py-1.5 text-right">{formatEuro(r.gross)}</td><td className="py-1.5 text-right">{formatEuro(r.kapest)}</td><td className="py-1.5 text-right">{formatEuro(r.solz)}</td><td className="py-1.5 text-right">{formatEuro(r.church)}</td><td className="py-1.5 text-right font-semibold">{formatEuro(r.net)}</td></tr>)}</tbody>
          </table></div>
          <p className="mt-3 text-xs text-slate-500">{team.distribution.note}</p>
        </Card>
      )}
    </>
  );
}
