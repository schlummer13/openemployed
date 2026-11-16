"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { ChecklistCard, type Checklist } from "@/components/Personnel";
import { hm } from "@/components/TimeClock";
import { Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Skeleton, Table, Textarea } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

interface Balance { employee_id: string; name: string; balance: number; over_cap: boolean; rule: string; start: string | null }
interface TplItem { title: string; offset: number | null; responsible: string | null; legal: string | null; description?: string | null }
interface Tpl { id: string; kind: "onboarding" | "offboarding"; name: string; items: TplItem[]; is_default: boolean }
interface ContractTpl { id: string; name: string; category: string; body: string }

const signed = (m: number) => (m > 0 ? "+" : "") + hm(m);

/** Tab „Personal“: Zeitkonten aller Mitarbeiter, offene On-/Offboardings, Vorlagen für Checklisten und Verträge. */
export function PersonnelOverview() {
  const [balances, setBalances] = useState<Balance[] | null>(null);
  const [lists, setLists] = useState<Checklist[]>([]);
  const [tpls, setTpls] = useState<Tpl[]>([]);
  const [contracts, setContracts] = useState<{ templates: ContractTpl[]; placeholders: string[] } | null>(null);
  const [editTpl, setEditTpl] = useState<{ id: string | null; kind: "onboarding" | "offboarding"; name: string; text: string } | null>(null);
  const [editContract, setEditContract] = useState<{ id: string | null; name: string; category: string; body: string } | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => {
    void api<Balance[]>("GET", "/api/personnel/time-accounts").then(setBalances);
    void api<Checklist[]>("GET", "/api/personnel/checklists?open=true").then(setLists);
    void api<Tpl[]>("GET", "/api/personnel/checklist-templates").then(setTpls);
    void api<{ templates: ContractTpl[]; placeholders: string[] }>("GET", "/api/personnel/contract-templates").then(setContracts);
  }, []);
  useEffect(load, [load]);

  // Checklisten-Vorlage als Text: eine Zeile je Punkt „Tage | Titel | Verantwortlich | Rechtsgrundlage“
  const toText = (items: TplItem[]) => items.map((i) => [i.offset ?? "", i.title, i.responsible ?? "", i.legal ?? ""].join(" | ")).join("\n");
  const fromText = (t: string): TplItem[] => t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const [o, title, r, legal] = l.split("|").map((x) => x.trim());
    const off = Number(o);
    return { offset: o === "" || Number.isNaN(off) ? null : off, title: title || o || "", responsible: r || null, legal: legal || null };
  });
  const saveTpl = (e: FormEvent) => {
    e.preventDefault();
    if (!editTpl) return;
    const body = { kind: editTpl.kind, name: editTpl.name, items: fromText(editTpl.text) };
    (editTpl.id ? api("PUT", `/api/personnel/checklist-templates/${editTpl.id}`, body) : api("POST", "/api/personnel/checklist-templates", body))
      .then(() => { toast("Vorlage gespeichert."); setEditTpl(null); load(); }).catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  const saveContract = (e: FormEvent) => {
    e.preventDefault();
    if (!editContract) return;
    const body = { name: editContract.name, category: editContract.category, body: editContract.body };
    (editContract.id ? api("PUT", `/api/personnel/contract-templates/${editContract.id}`, body) : api("POST", "/api/personnel/contract-templates", body))
      .then(() => { toast("Vertragsvorlage gespeichert."); setEditContract(null); load(); }).catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };

  if (!balances) return <Skeleton className="h-64" />;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Zeitkonten" description="Saldo aus Stempeluhr, Soll laut Vertrag, Abwesenheiten und Buchungen. Klick öffnet die Personalseite." />
        {balances.length === 0 ? <div className="p-5"><Empty>Keine Mitarbeiter.</Empty></div> : (
          <Table head={<tr><th>Mitarbeiter</th><th>Regel</th><th className="text-right">Saldo</th><th /></tr>}>
            {balances.map((b) => (
              <tr key={b.employee_id}>
                <td className="font-medium"><Link className="hover:underline" href={`/mitarbeiter/${b.employee_id}`}>{b.name}</Link></td>
                <td className="text-xs text-slate-500">{b.rule === "payout" ? "Auszahlung" : b.rule === "included" ? "abgegolten" : "Zeitkonto"}{!b.start && " · noch keine Stempelzeiten"}</td>
                <td className={`text-right font-mono tabular-nums ${b.balance > 0 ? "text-emerald-600" : b.balance < 0 ? "text-rose-600" : ""}`}>{signed(b.balance)}</td>
                <td className="text-right">{b.over_cap && <Badge tone="amber">über Grenze</Badge>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <div>
        <h2 className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-300">Offene On-/Offboardings</h2>
        {lists.length === 0 ? <Card className="p-5"><Empty>Alles erledigt.</Empty></Card> : (
          <div className="grid gap-4 xl:grid-cols-2">{lists.map((c) => <ChecklistCard key={c.id} c={c} onChange={load} showEmployee />)}</div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Checklisten-Vorlagen" description="Werden beim Anlegen (Onboarding) und beim Eintragen des Austritts (Offboarding) automatisch gestartet."
            actions={<Button onClick={() => setEditTpl({ id: null, kind: "onboarding", name: "", text: "" })}>+ Vorlage</Button>} />
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {tpls.map((t) => (
              <li key={t.id} className="flex items-center gap-3 px-5 py-3 text-sm">
                <Badge tone={t.kind === "onboarding" ? "green" : "amber"}>{t.kind === "onboarding" ? "Onboarding" : "Offboarding"}</Badge>
                <span className="min-w-0 flex-1 truncate font-medium">{t.name}{t.is_default && <span className="font-normal text-slate-500"> · Standard</span>}</span>
                <span className="text-xs text-slate-500">{t.items.length} Punkte</span>
                <Button className="!px-2 !py-1 text-xs" onClick={() => setEditTpl({ id: t.id, kind: t.kind, name: t.name, text: toText(t.items) })}>Bearbeiten</Button>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Vertragsvorlagen" description="Mit Platzhaltern wie {{mitarbeiter.name}} – erzeugt ein PDF in der Personalakte, optional digital signiert."
            actions={<Button onClick={() => setEditContract({ id: null, name: "", category: "contract", body: "# Titel\n\n" })}>+ Vorlage</Button>} />
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {(contracts?.templates ?? []).map((t) => (
              <li key={t.id} className="flex items-center gap-3 px-5 py-3 text-sm">
                <span className="min-w-0 flex-1 truncate font-medium">{t.name}</span>
                <Button className="!px-2 !py-1 text-xs" onClick={() => setEditContract({ id: t.id, name: t.name, category: t.category, body: t.body })}>Bearbeiten</Button>
              </li>
            ))}
          </ul>
          <p className="px-5 pb-4 text-xs text-slate-500">Muster ohne Gewähr – Arbeitsverträge vor der ersten Nutzung anwaltlich prüfen lassen.</p>
        </Card>
      </div>

      <Drawer open={!!editTpl} onClose={() => setEditTpl(null)} size="lg" title="Checklisten-Vorlage"
        footer={<><Button onClick={() => setEditTpl(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="tpl-form">Speichern</Button></>}>
        {editTpl && (
          <form id="tpl-form" onSubmit={saveTpl} className="grid gap-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name"><Input required value={editTpl.name} onChange={(e) => setEditTpl({ ...editTpl, name: e.target.value })} /></Field>
              <Field label="Art"><Select value={editTpl.kind} disabled={!!editTpl.id} onChange={(e) => setEditTpl({ ...editTpl, kind: e.target.value as "onboarding" | "offboarding" })}>
                <option value="onboarding">Onboarding</option><option value="offboarding">Offboarding</option></Select></Field>
            </div>
            <Field label="Punkte" hint="Eine Zeile je Punkt: Tage relativ zum Ein-/Austritt | Titel | Verantwortlich | Rechtsgrundlage (z. B. „42 | Anmeldung SV | Personal | § 6 DEÜV“)">
              <Textarea rows={16} className="font-mono text-xs" value={editTpl.text} onChange={(e) => setEditTpl({ ...editTpl, text: e.target.value })} />
            </Field>
          </form>
        )}
      </Drawer>
      <Drawer open={!!editContract} onClose={() => setEditContract(null)} size="xl" title="Vertragsvorlage"
        footer={<><Button onClick={() => setEditContract(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="ct-form">Speichern</Button></>}>
        {editContract && (
          <form id="ct-form" onSubmit={saveContract} className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]">
            <div className="grid gap-4">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Name"><Input required value={editContract.name} onChange={(e) => setEditContract({ ...editContract, name: e.target.value })} /></Field>
                <Field label="Ablage als"><Select value={editContract.category} onChange={(e) => setEditContract({ ...editContract, category: e.target.value })}>
                  <option value="contract">Arbeitsvertrag</option><option value="amendment">Vertragsänderung</option><option value="reference">Zeugnis</option>
                  <option value="termination">Kündigung/Aufhebung</option><option value="other">Sonstiges</option></Select></Field>
              </div>
              <Field label="Text (Markdown: # Überschrift, **fett**, - Liste)"><Textarea rows={26} className="font-mono text-xs" value={editContract.body} onChange={(e) => setEditContract({ ...editContract, body: e.target.value })} /></Field>
            </div>
            <div>
              <p className="mb-2 text-[13px] font-medium text-slate-700 dark:text-slate-300">Platzhalter</p>
              <div className="flex flex-wrap gap-1.5">
                {(contracts?.placeholders ?? []).map((p) => (
                  <button key={p} type="button" onClick={() => setEditContract({ ...editContract, body: `${editContract.body}{{${p}}}` })}
                    className="rounded-md bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-700 hover:bg-brand-100 dark:bg-slate-800 dark:text-slate-300">{`{{${p}}}`}</button>
                ))}
              </div>
            </div>
          </form>
        )}
      </Drawer>
    </div>
  );
}
