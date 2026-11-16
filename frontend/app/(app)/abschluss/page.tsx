"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Icon } from "@/components/Icon";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Field, Input, linkButtonClass, PageHeader, PageSkeleton, Select } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

type Kind = "tax_provision" | "closing_costs" | "other_provision" | "prepaid" | "deferred";
interface Line { label: string; amount: string; accounts: { account: string; name: string; amount: string }[] }
interface Overview {
  statements: { year: number; chart: string; aktiva: Line[]; passiva: Line[]; total_aktiva: string; total_passiva: string; balanced: boolean; guv: Line[]; result: string; note: string };
  checklist: { items: { key: string; label: string; done: boolean; hint: string; href: string | null; optional?: boolean }[]; closed: boolean; ready: boolean };
  entries: { id: string; kind: Kind; label: string; amount: string; account: string | null; description: string; entry_number: string | null; reversal_number: string | null; created_at: string; created_by: string }[];
  tax_proposal: { kst: string; solz: string; gewst: string };
}
const KINDS: Record<Kind, { label: string; hint: string; account?: "expense" | "revenue" }> = {
  tax_provision: { label: "Steuerrückstellungen (KSt, Soli, GewSt)", hint: "Vorschlag aus der Steuerschätzung des Jahres – Beträge bei Bedarf anpassen." },
  closing_costs: { label: "Rückstellung für Abschlusskosten", hint: "Voraussichtliche Kosten des Steuerberaters für diesen Jahresabschluss." },
  other_provision: { label: "Sonstige Rückstellung", hint: "Ungewisse Verbindlichkeiten, z. B. ausstehende Rechnungen für Leistungen des Jahres.", account: "expense" },
  prepaid: { label: "Aktive Rechnungsabgrenzung", hint: "Im Voraus bezahlter Aufwand für das Folgejahr (z. B. Jahreslizenz). Wird am 1.1. automatisch aufgelöst.", account: "expense" },
  deferred: { label: "Passive Rechnungsabgrenzung", hint: "Im Voraus berechnete Erlöse für das Folgejahr. Wird am 1.1. automatisch aufgelöst.", account: "revenue" },
};

function Statement({ title, lines, total }: { title: string; lines: Line[]; total?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-slate-500">{title}</h3>
      <ul className="divide-y divide-slate-100 text-sm dark:divide-white/5">
        {lines.map((l) => (
          <li key={l.label}>
            <button type="button" disabled={!l.accounts.length} onClick={() => setOpen(open === l.label ? null : l.label)}
              className="flex w-full items-baseline justify-between gap-3 py-2 text-left disabled:cursor-default">
              <span className="flex min-w-0 items-center gap-1.5">{l.accounts.length > 0 && <Icon name="chevron" className={`size-3 shrink-0 text-slate-400 transition ${open === l.label ? "rotate-90" : ""}`} />}<span>{l.label}</span></span>
              <span className={`shrink-0 tabular ${Number(l.amount) < 0 ? "text-red-600 dark:text-red-400" : ""}`}>{formatEuro(l.amount)}</span>
            </button>
            {open === l.label && (
              <ul className="mb-2 ml-5 space-y-0.5 text-xs text-slate-500">
                {l.accounts.map((a) => <li key={a.account} className="flex justify-between gap-3"><span><span className="tabular">{a.account}</span> {a.name}</span><span className="tabular">{formatEuro(a.amount)}</span></li>)}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {total !== undefined && <p className="mt-1 flex justify-between border-t-2 border-slate-200 pt-2 text-sm font-semibold dark:border-white/10"><span>Summe</span><span className="tabular">{formatEuro(total)}</span></p>}
    </div>
  );
}

export default function YearEndPage() {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<{ number: string; name: string; type: string }[]>([]);
  const [form, setForm] = useState<{ kind: Kind; description: string; amount: string; account: string; kst: string; solz: string; gewst: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { setData(null); void api<Overview>("GET", `/api/year-end/${year}`).then(setData); }, [year]);
  useEffect(load, [load]);
  useEffect(() => { void api<{ accounts: { number: string; name: string; type: string }[] }>("GET", "/api/accounting/accounts").then((r) => setAccounts(r.accounts)); }, []);

  const open = (kind: Kind) => setForm({ kind, description: KINDS[kind].label, amount: "", account: "",
    kst: (data?.tax_proposal.kst ?? "0").replace(".", ","), solz: (data?.tax_proposal.solz ?? "0").replace(".", ","), gewst: (data?.tax_proposal.gewst ?? "0").replace(".", ",") });
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setBusy(true); setError(null);
    const body = form.kind === "tax_provision"
      ? { kind: form.kind, description: form.description, kst: toApiDecimal(form.kst), solz: toApiDecimal(form.solz), gewst: toApiDecimal(form.gewst) }
      : { kind: form.kind, description: form.description, amount: toApiDecimal(form.amount), account: form.account || null };
    api("POST", `/api/year-end/${year}/entries`, body)
      .then(() => { toast("Abschlussbuchung zum 31.12. gebucht und festgeschrieben."); setForm(null); load(); })
      .catch((err: unknown) => setError(err instanceof HttpError ? err.message : "Buchen fehlgeschlagen"))
      .finally(() => setBusy(false));
  };
  const lock = async () => {
    if (!data) return;
    const open = data.checklist.items.filter((i) => !i.done && !i.optional);
    if (!(await confirm({ title: `Jahresabschluss ${year} festschreiben?`, tone: "danger", confirmLabel: "Festschreiben",
      message: <>Danach sind keine Abschlussbuchungen für {year} mehr möglich (Abschlussperiode {year}-13).{open.length > 0 && <><br /><br /><strong>Noch offen:</strong> {open.map((i) => i.label).join(", ")}. Die Festschreibung trotz offener Punkte wird protokolliert.</>}</> }))) return;
    try {
      await api("POST", `/api/year-end/${year}/lock`, { confirm_open: open.length > 0 });
      toast(`Jahresabschluss ${year} festgeschrieben.`); load();
    } catch (err) { toast(err instanceof HttpError ? err.message : "Festschreiben fehlgeschlagen", "error"); }
  };

  if (!data) return <PageSkeleton stats={0} />;
  const st = data.statements;
  const done = data.checklist.items.filter((i) => i.done).length;
  const acctOptions = form && KINDS[form.kind].account ? accounts.filter((a) => a.type === KINDS[form.kind].account) : [];

  return (
    <>
      <PageHeader title="Jahresabschluss" description="Vorbereitung für den Steuerberater: Checkliste, Abschlussbuchungen zum 31.12., Bilanz und GuV als Entwurf, Übergabepaket."
        actions={<>
          <Select aria-label="Geschäftsjahr" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28">{[0, 1, 2, 3].map((d) => new Date().getFullYear() - d).map((y) => <option key={y} value={y}>{y}</option>)}</Select>
          <a className={linkButtonClass} href={`/api/year-end/${year}/export`}><Icon name="file" />Übergabepaket (ZIP)</a>
          {!data.checklist.closed && <Button variant="primary" onClick={lock}>Festschreiben</Button>}
        </>} />
      {data.checklist.closed && <div className="mb-6"><Alert kind="success">Jahresabschluss {year} ist festgeschrieben – Abschlussbuchungen sind gesperrt.</Alert></div>}

      <div className="mb-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Card>
          <CardHeader title="Abschluss-Checkliste" description={`${done} von ${data.checklist.items.length} erledigt`} />
          <ul className="space-y-3">
            {data.checklist.items.map((i) => (
              <li key={i.key} className="flex items-start gap-3 text-sm">
                <span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full ${i.done ? "bg-emerald-500 text-white" : i.optional ? "ring-1 ring-inset ring-slate-300 dark:ring-white/20" : "ring-2 ring-inset ring-amber-400"}`}>{i.done && <Icon name="check" className="size-2.5" />}</span>
                <span className="min-w-0">
                  {i.href && !i.done ? <Link href={i.href} className="font-medium hover:underline">{i.label}</Link> : <span className="font-medium">{i.label}</span>}
                  {i.optional && <span className="ml-1.5 text-xs text-slate-400">(falls zutreffend)</span>}
                  <span className="block text-xs text-slate-500">{i.hint}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Abschlussbuchungen zum 31.12." description="Gebucht in die Abschlussperiode – unabhängig von der Umsatzsteuer-Sperre des Dezembers."
            actions={!data.checklist.closed ? <Select aria-label="Abschlussbuchung hinzufügen" value="" onChange={(e) => e.target.value && open(e.target.value as Kind)} className="w-56"><option value="">+ Abschlussbuchung …</option>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select> : undefined} />
          {data.entries.length === 0 ? <p className="py-6 text-center text-sm text-slate-500">Noch keine Abschlussbuchungen für {year}.</p> : (
            <ul className="divide-y divide-slate-100 text-sm dark:divide-white/5">
              {data.entries.map((e) => (
                <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-3 py-2.5">
                  <span className="min-w-0"><span className="font-medium">{e.description}</span><span className="block text-xs text-slate-500">{e.label}{e.account ? ` · Konto ${e.account}` : ""} · {e.entry_number}{e.reversal_number ? ` · Auflösung ${e.reversal_number} am 01.01.${year + 1}` : ""} · {formatDate(e.created_at.slice(0, 10))}</span></span>
                  <span className="tabular font-medium">{formatEuro(e.amount)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title={`Bilanz zum 31.12.${year}`} description={st.balanced ? "Aktiva = Passiva ✓" : "Achtung: Aktiva ≠ Passiva"} actions={<Badge tone={st.balanced ? "green" : "red"}>{st.balanced ? "ausgeglichen" : "nicht ausgeglichen"}</Badge>} />
          <div className="grid gap-6 sm:grid-cols-2">
            <Statement title="Aktiva" lines={st.aktiva} total={st.total_aktiva} />
            <Statement title="Passiva" lines={st.passiva} total={st.total_passiva} />
          </div>
        </Card>
        <Card>
          <CardHeader title={`Gewinn- und Verlustrechnung ${year}`} description="Gesamtkostenverfahren (§ 275 Abs. 2 HGB), vereinfacht" />
          <Statement title="GuV" lines={st.guv} />
          <p className="mt-2 flex justify-between border-t-2 border-slate-200 pt-2 text-sm font-semibold dark:border-white/10"><span>Jahresüberschuss/-fehlbetrag</span><span className={`tabular ${Number(st.result) < 0 ? "text-red-600" : ""}`}>{formatEuro(st.result)}</span></p>
        </Card>
      </div>
      <p className="mt-4 text-xs text-slate-500">{st.note} Kontenrahmen {st.chart}.</p>

      <Drawer open={!!form} onClose={() => { setForm(null); setError(null); }} title={form ? KINDS[form.kind].label : ""} subtitle={form ? KINDS[form.kind].hint : undefined}
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="closing-form" variant="primary" disabled={busy}>Zum 31.12.{year} buchen</Button></>}>
        {form && (
          <form id="closing-form" onSubmit={save} className="grid gap-4">
            <Field label="Buchungstext"><Input required value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
            {form.kind === "tax_provision" ? (
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Körperschaftsteuer (€)"><Input inputMode="decimal" value={form.kst} onChange={(e) => setForm({ ...form, kst: e.target.value })} /></Field>
                <Field label="Solidaritätszuschlag (€)"><Input inputMode="decimal" value={form.solz} onChange={(e) => setForm({ ...form, solz: e.target.value })} /></Field>
                <Field label="Gewerbesteuer (€)"><Input inputMode="decimal" value={form.gewst} onChange={(e) => setForm({ ...form, gewst: e.target.value })} /></Field>
              </div>
            ) : (
              <>
                <Field label="Betrag (€)"><Input required inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
                {KINDS[form.kind].account && (
                  <Field label={KINDS[form.kind].account === "revenue" ? "Erlöskonto" : "Aufwandskonto"}>
                    <Select required value={form.account} onChange={(e) => setForm({ ...form, account: e.target.value })}><option value="">– auswählen –</option>{acctOptions.map((a) => <option key={a.number} value={a.number}>{a.number} {a.name}</option>)}</Select>
                  </Field>
                )}
              </>
            )}
            {error && <Alert>{error}</Alert>}
            <p className="text-xs text-slate-500">Die Buchung wird festgeschrieben; eine Korrektur ist nur per Gegenbuchung möglich. Kontenzuordnung mit dem Steuerberater abstimmen.</p>
          </form>
        )}
      </Drawer>
    </>
  );
}
