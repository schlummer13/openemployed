"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Stat, Table, Textarea } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

type Method = "linear" | "digital_full" | "pool" | "gwg";
type AssetClass = "digital" | "furniture" | "vehicle" | "machine" | "intangible" | "other";
interface AssetRow {
  id: string; number: number; name: string; account: string; asset_class: AssetClass; method: Method; acquisition_date: string; cost: string;
  useful_life_months: number; receipt_id: string | null; disposal_date: string | null; disposal_note: string | null; notes: string | null;
  depreciation: string; disposal_book_value: string; book_value_start: string; book_value_end: string; accumulated_start: string;
  accumulated_end: string; addition: string; disposal: string; posted: boolean; has_postings: boolean;
}
interface Candidate { id: string; receipt_date: string; description: string; net_amount: string; expense_account: string | null; category: string; supplier_name: string }
interface Data { year: number; assets: AssetRow[]; candidates: Candidate[]; accounts: { number: string; name: string }[]; useful_life: Record<AssetClass, number> }

const CLASSES: Record<AssetClass, string> = {
  digital: "Computer, Software (digital)", furniture: "Büromöbel, Einrichtung", vehicle: "Fahrzeug", machine: "Maschinen, Geräte",
  intangible: "Immaterielles (Lizenzen, Rechte)", other: "Sonstiges",
};
const METHODS: Record<Method, string> = {
  linear: "Linear (monatsgenau)", digital_full: "Digital: voll im Anschaffungsjahr", pool: "Sammelposten (5 × 20 %)", gwg: "GWG: voll im Anschaffungsjahr",
};
const METHOD_HINT: Record<Method, string> = {
  linear: "§ 7 Abs. 1 EStG – Anschaffungsmonat zählt voll, im Abgangsjahr bis zum Abgangsmonat.",
  digital_full: "Computerhardware und Software mit Nutzungsdauer 1 Jahr (BMF-Schreiben 22.02.2022).",
  pool: "Für 250,01 € bis 1.000 € netto (§ 6 Abs. 2a EStG); bleibt auch bei Abgang bestehen.",
  gwg: "Bis 800 € netto (§ 6 Abs. 2 EStG).",
};
type Form = { id: string | null; name: string; account: string; asset_class: AssetClass; method: Method; acquisition_date: string; cost: string;
  useful_life_months: string; receipt_id: string | null; disposal_date: string; disposal_note: string; notes: string; locked: boolean };

export default function AssetsPage() {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<Data | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<Data>("GET", `/api/assets?year=${year}`).then(setData); }, [year]);
  useEffect(load, [load]);

  const suggestMethod = (cost: number, cls: AssetClass): Method => (cost <= 800 ? "gwg" : cls === "digital" ? "digital_full" : "linear");
  const newForm = (c?: Candidate): Form => {
    const cost = c ? Number(c.net_amount) : 0;
    const account = c?.expense_account ?? data?.accounts.find((a) => a.number === "0690" || a.number === "0490")?.number ?? data?.accounts[0]?.number ?? "";
    return { id: null, name: c?.description ?? "", account, asset_class: "other", method: c ? suggestMethod(cost, "other") : "linear",
      acquisition_date: c?.receipt_date ?? new Date().toISOString().slice(0, 10), cost: c ? c.net_amount.replace(".", ",") : "",
      useful_life_months: String(data?.useful_life.other ?? 60), receipt_id: c?.id ?? null, disposal_date: "", disposal_note: "", notes: "", locked: false };
  };
  const setClass = (cls: AssetClass) => form && setForm({ ...form, asset_class: cls, useful_life_months: form.locked ? form.useful_life_months : String(data?.useful_life[cls] ?? 60),
    method: form.locked ? form.method : suggestMethod(Number(toApiDecimal(form.cost) || 0), cls) });

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setBusy(true); setError(null);
    const body = { name: form.name, account: form.account, asset_class: form.asset_class, method: form.method, acquisition_date: form.acquisition_date,
      cost: toApiDecimal(form.cost), useful_life_months: Number(form.useful_life_months), receipt_id: form.receipt_id,
      disposal_date: form.disposal_date || null, disposal_note: form.disposal_note || null, notes: form.notes || null };
    (form.id ? api("PUT", `/api/assets/${form.id}`, body) : api("POST", "/api/assets", body))
      .then(() => { toast(form.id ? "Anlagegut gespeichert." : "Anlagegut ins Verzeichnis aufgenommen."); setForm(null); load(); })
      .catch((err: unknown) => setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen"))
      .finally(() => setBusy(false));
  };
  const runDepreciation = async () => {
    if (!data) return;
    const open = data.assets.filter((a) => !a.posted && (Number(a.depreciation) > 0 || Number(a.disposal_book_value) > 0));
    const sum = open.reduce((s, a) => s + Number(a.depreciation) + Number(a.disposal_book_value), 0);
    if (!open.length) { toast(`Für ${year} ist keine Abschreibung offen.`, "info"); return; }
    if (!(await confirm({ title: `Abschreibung ${year} buchen?`, confirmLabel: "Buchen",
      message: <>{open.length} Anlagegüter, zusammen <strong>{formatEuro(sum.toFixed(2))}</strong>. Gebucht wird als Abschlussbuchung zum 31.12.{year} (Abschlussperiode) und festgeschrieben.</> }))) return;
    setBusy(true);
    try {
      const r = await api<{ posted: unknown[]; total: string }>("POST", "/api/assets/depreciation", { year });
      toast(`${r.posted.length} Abschreibungen über ${formatEuro(r.total)} gebucht.`);
      load();
    } catch (err) { toast(err instanceof HttpError ? err.message : "Buchen fehlgeschlagen", "error"); } finally { setBusy(false); }
  };

  if (!data) return <PageSkeleton />;
  const active = data.assets.filter((a) => a.acquisition_date.slice(0, 4) <= String(year));
  const sum = (k: keyof AssetRow) => active.reduce((s, a) => s + Number(a[k]), 0).toFixed(2);
  const openDep = active.filter((a) => !a.posted && (Number(a.depreciation) > 0 || Number(a.disposal_book_value) > 0)).length;
  const acc = Object.fromEntries(data.accounts.map((a) => [a.number, a.name]));

  return (
    <>
      <PageHeader title="Anlagen" description="Anlagenverzeichnis und Abschreibung (AfA). Die Jahresabschreibung wird als Abschlussbuchung zum 31.12. gebucht."
        actions={<>
          <Select aria-label="Jahr" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28">{[0, 1, 2, 3].map((d) => new Date().getFullYear() - d).map((y) => <option key={y} value={y}>{y}</option>)}</Select>
          <Button onClick={() => setForm(newForm())}>+ Anlagegut</Button>
          <Button variant="primary" onClick={runDepreciation} disabled={busy || openDep === 0}>Abschreibung {year} buchen{openDep ? ` (${openDep})` : ""}</Button>
        </>} />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={`Buchwert 31.12.${year}`} icon="building" tone="brand" value={formatEuro(sum("book_value_end"))} hint={`${active.length} Anlagegüter`} />
        <Stat label={`Zugänge ${year}`} icon="trend" tone="green" value={formatEuro(sum("addition"))} />
        <Stat label={`Abschreibung ${year}`} icon="percent" tone="amber" value={formatEuro(sum("depreciation"))} hint={openDep ? `${openDep} noch nicht gebucht` : "vollständig gebucht"} />
        <Stat label={`Abgänge ${year}`} icon="x" value={formatEuro(sum("disposal"))} />
      </div>

      {data.candidates.length > 0 && (
        <Card className="mb-6">
          <h2 className="flex items-center gap-2 font-semibold">Aus Belegen übernehmen <Badge tone="amber">{data.candidates.length}</Badge></h2>
          <p className="mb-3 mt-0.5 text-[13px] text-slate-500">Gebuchte Belege auf Anlagekonten, die noch nicht im Anlagenverzeichnis stehen.</p>
          <ul className="divide-y divide-slate-100 dark:divide-white/5">
            {data.candidates.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                <span className="min-w-0"><span className="font-medium">{c.supplier_name}</span> · {c.description} <span className="tabular text-slate-500">· {formatDate(c.receipt_date)} · {formatEuro(c.net_amount)} netto</span></span>
                <Button variant="primary" className="!h-8" onClick={() => setForm(newForm(c))}>Aufnehmen</Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <h2 className="mb-3 font-semibold">Anlagenspiegel {year}</h2>
        {active.length === 0 ? <Empty>Noch keine Anlagegüter für {year}.</Empty> : (
          <Table head={<tr><th>Nr.</th><th>Anlagegut</th><th>Angeschafft</th><th className="text-right">Anschaffungskosten</th><th className="text-right">Buchwert 1.1.</th><th className="text-right">Zugang</th><th className="text-right">AfA {year}</th><th className="text-right">Buchwert 31.12.</th><th>Status</th><th /></tr>}>
            {active.map((a) => (
              <tr key={a.id}>
                <td className="tabular">AV-{String(a.number).padStart(4, "0")}</td>
                <td>{a.name}<div className="text-xs text-slate-500">{a.account} {acc[a.account]} · {METHODS[a.method]}{a.method === "linear" ? ` · ${Math.round(a.useful_life_months / 12 * 10) / 10} J.` : ""}</div></td>
                <td className="tabular">{formatDate(a.acquisition_date)}{a.disposal_date && <div className="text-xs text-red-600">Abgang {formatDate(a.disposal_date)}</div>}</td>
                <td className="text-right tabular">{formatEuro(a.cost)}</td>
                <td className="text-right tabular">{formatEuro(a.book_value_start)}</td>
                <td className="text-right tabular">{Number(a.addition) ? formatEuro(a.addition) : "–"}</td>
                <td className="text-right tabular">{formatEuro(a.depreciation)}{Number(a.disposal_book_value) > 0 && <div className="text-xs text-red-600">+ Abgang {formatEuro(a.disposal_book_value)}</div>}</td>
                <td className="text-right tabular font-medium">{formatEuro(a.book_value_end)}</td>
                <td>{a.posted ? <Badge tone="green">gebucht</Badge> : Number(a.depreciation) > 0 || Number(a.disposal_book_value) > 0 ? <Badge tone="amber">offen</Badge> : <Badge>–</Badge>}</td>
                <td className="text-right"><Button className="!h-7 !text-xs" onClick={() => setForm({ ...a, cost: a.cost.replace(".", ","), useful_life_months: String(a.useful_life_months), disposal_date: a.disposal_date ?? "", disposal_note: a.disposal_note ?? "", notes: a.notes ?? "", locked: a.has_postings })}>Bearbeiten</Button></td>
              </tr>
            ))}
          </Table>
        )}
        <p className="mt-3 text-xs text-slate-500">GWG bis 250 € netto werden direkt als Aufwand gebucht und erscheinen hier nicht. Nutzungsdauern nach AfA-Tabelle – mit dem Steuerberater abstimmen.</p>
      </Card>

      <Drawer open={!!form} onClose={() => { setForm(null); setError(null); }} size="lg" title={form?.id ? "Anlagegut bearbeiten" : "Anlagegut aufnehmen"}
        subtitle={form?.locked ? "Bereits abgeschrieben – Rechengrundlagen sind fest, Name, Notizen und Abgang bleiben änderbar." : "Anschaffungskosten netto (bei Vorsteuerabzug)."}
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="asset-form" variant="primary" disabled={busy}>Speichern</Button></>}>
        {form && (
          <form id="asset-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Bezeichnung" className="sm:col-span-2"><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Art"><Select value={form.asset_class} disabled={form.locked} onChange={(e) => setClass(e.target.value as AssetClass)}>{Object.entries(CLASSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="Anlagekonto"><Select value={form.account} disabled={form.locked} onChange={(e) => setForm({ ...form, account: e.target.value })}>{data.accounts.map((a) => <option key={a.number} value={a.number}>{a.number} {a.name}</option>)}</Select></Field>
            <Field label="Anschaffungsdatum"><Input type="date" required disabled={form.locked} value={form.acquisition_date} onChange={(e) => setForm({ ...form, acquisition_date: e.target.value })} /></Field>
            <Field label="Anschaffungskosten netto (€)"><Input required inputMode="decimal" disabled={form.locked} value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} /></Field>
            <Field label="Abschreibung" className="sm:col-span-2" hint={METHOD_HINT[form.method]}>
              <Select value={form.method} disabled={form.locked} onChange={(e) => setForm({ ...form, method: e.target.value as Method })}>{Object.entries(METHODS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
            </Field>
            {form.method === "linear" && <Field label="Nutzungsdauer (Monate)" hint={`= ${(Number(form.useful_life_months) / 12).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Jahre`}><Input type="number" min={1} max={600} required disabled={form.locked} value={form.useful_life_months} onChange={(e) => setForm({ ...form, useful_life_months: e.target.value })} /></Field>}
            <Field label="Abgang am (Verkauf/Verschrottung)"><Input type="date" value={form.disposal_date} onChange={(e) => setForm({ ...form, disposal_date: e.target.value })} /></Field>
            {form.disposal_date && <Field label="Grund des Abgangs" className="sm:col-span-2"><Input value={form.disposal_note} onChange={(e) => setForm({ ...form, disposal_note: e.target.value })} /></Field>}
            <Field label="Notizen" className="sm:col-span-2"><Textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
            {error && <div className="sm:col-span-2"><Alert>{error}</Alert></div>}
          </form>
        )}
      </Drawer>
    </>
  );
}
