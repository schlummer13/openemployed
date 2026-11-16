"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Drawer, Field, Input, PageHeader, PageSkeleton, Select, Stat, Textarea } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

type Stage = "new" | "contacted" | "qualified" | "offer" | "won" | "lost";
interface Lead {
  id: string; number: number; title: string; company: string | null; contact_name: string | null; email: string | null; phone: string | null;
  source: string | null; stage: Stage; value: string; recurring: boolean; probability: number | null; expected_close: string | null;
  owner_id: string | null; owner_name?: string | null; customer_id: string | null; customer_number?: string | null; offer_id: string | null;
  offer_number?: string | null; offer_status?: string | null; lost_reason: string | null; notes: string | null; next_due?: string | null; overdue?: boolean; weight?: number;
}
interface Activity { id: string; kind: string; text: string; due_date: string | null; done_at: string | null; created_by: string; created_at: string }
interface Board { stages: { key: Stage; label: string; probability: number }[]; leads: Lead[]; stats: { open: number; pipeline: string; weighted: string; won_year: number; lost_year: number; won_value_year: string; win_rate: number | null } }
interface Person { id: string; name: string }

const KIND: Record<string, string> = { note: "Notiz", call: "Anruf", email: "E-Mail", meeting: "Termin", task: "Aufgabe", system: "System" };
const EMPTY = { title: "", company: "", contact_name: "", email: "", phone: "", source: "", value: "", recurring: false, probability: "", expected_close: "", owner_id: "", notes: "" };
type Form = typeof EMPTY & { id: string | null };

export default function SalesPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [detail, setDetail] = useState<(Lead & { activities: Activity[] }) | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [act, setAct] = useState({ kind: "note", text: "", due_date: "" });
  const [addr, setAddr] = useState<{ street: string; postal_code: string; city: string; country_code: string; vat_id: string } | null>(null);
  const { toast, prompt, confirm } = useFeedback();
  const load = useCallback(() => { void api<Board>("GET", "/api/crm").then(setBoard); }, []);
  useEffect(load, [load]);
  useEffect(() => { void api<{ shareholders: Person[] }>("GET", "/api/team").then((t) => setPeople(t.shareholders)).catch(() => undefined); }, []);
  const open = async (id: string) => setDetail(await api("GET", `/api/crm/leads/${id}`));
  const run = async (fn: () => Promise<unknown>, msg?: string) => {
    try { await fn(); if (msg) toast(msg); load(); if (detail) void open(detail.id); } catch (e) { toast(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen", "error"); }
  };
  const moveTo = async (lead: Lead, stage: Stage) => {
    if (stage === lead.stage) return;
    let reason: string | null = null;
    if (stage === "lost") { reason = await prompt({ title: "Warum verloren?", input: { label: "Grund (z. B. Preis, Wettbewerber, kein Bedarf)", minLength: 2 } }); if (!reason) return; }
    await run(() => api("POST", `/api/crm/leads/${lead.id}/move`, { stage, lost_reason: reason }));
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    const body = { title: form.title, company: form.company || null, contact_name: form.contact_name || null, email: form.email || null, phone: form.phone || null,
      source: form.source || null, value: toApiDecimal(form.value || "0"), recurring: form.recurring, probability: form.probability === "" ? null : Number(form.probability),
      expected_close: form.expected_close || null, owner_id: form.owner_id || null, notes: form.notes || null };
    await run(async () => { if (form.id) await api("PUT", `/api/crm/leads/${form.id}`, body); else await api("POST", "/api/crm/leads", body); setForm(null); }, "Lead gespeichert.");
  };

  if (!board) return <PageSkeleton stats={4} />;
  const s = board.stats;
  return (
    <>
      <PageHeader title="Vertrieb" eyebrow="Vertrieb" description="Von der ersten Anfrage bis zum unterschriebenen Vertrag: Leads per Drag & Drop durch die Phasen ziehen, Angebote direkt aus dem Lead erstellen – angenommene Angebote setzen den Lead automatisch auf „gewonnen“."
        actions={<Button variant="primary" onClick={() => setForm({ ...EMPTY, id: null })}>+ Lead</Button>} />
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Offene Pipeline" icon="trend" tone="brand" value={formatEuro(s.pipeline)} hint={`${s.open} Leads`} />
        <Stat label="Gewichtet" icon="scale" value={formatEuro(s.weighted)} hint="nach Phase bzw. Wahrscheinlichkeit" />
        <Stat label="Gewonnen (Jahr)" icon="check" value={formatEuro(s.won_value_year)} hint={`${s.won_year} Abschlüsse`} />
        <Stat label="Abschlussquote" icon="pie" value={s.win_rate == null ? "–" : `${s.win_rate} %`} hint={`${s.lost_year} verloren`} />
      </div>
      <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6">
        <div className="grid min-w-[1100px] grid-cols-6 gap-3">
          {board.stages.map((st) => {
            const items = board.leads.filter((l) => l.stage === st.key);
            const sum = items.reduce((a, l) => a + Number(l.value), 0);
            return (
              <div key={st.key} onDragOver={(e) => e.preventDefault()} onDrop={() => { const l = board.leads.find((x) => x.id === dragId); if (l) void moveTo(l, st.key); setDragId(null); }}
                className={`flex min-h-[18rem] flex-col rounded-xl p-2 ${st.key === "won" ? "bg-emerald-50/70 dark:bg-emerald-500/5" : st.key === "lost" ? "bg-slate-100/70 dark:bg-white/[0.02]" : "bg-slate-100 dark:bg-white/[0.04]"}`}>
                <div className="mb-2 flex items-baseline justify-between px-1.5"><span className="text-[13px] font-semibold">{st.label} <span className="text-slate-400">{items.length}</span></span><span className="text-[11px] tabular text-slate-500">{formatEuro(sum)}</span></div>
                <div className="flex flex-col gap-2">
                  {items.map((l) => (
                    <button key={l.id} type="button" draggable onDragStart={() => setDragId(l.id)} onClick={() => void open(l.id)}
                      className="rounded-lg bg-white p-3 text-left shadow-card transition hover:shadow-pop dark:bg-slate-900">
                      <span className="block text-sm font-medium leading-snug">{l.title}</span>
                      <span className="block truncate text-xs text-slate-500">{l.company ?? l.contact_name ?? "–"}</span>
                      <span className="mt-2 flex items-center justify-between gap-2 text-xs"><span className="font-semibold tabular">{formatEuro(l.value)}{l.recurring ? "/Monat" : ""}</span>
                        {l.owner_name && <span className="truncate text-slate-500">{l.owner_name}</span>}</span>
                      {(l.next_due || l.offer_number) && <span className="mt-1 flex flex-wrap gap-1">
                        {l.next_due && <Badge tone={l.overdue ? "red" : "slate"}>Aufgabe {formatDate(l.next_due)}</Badge>}
                        {l.offer_number && <Badge tone="blue">{l.offer_number}</Badge>}</span>}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <Drawer open={!!form} onClose={() => setForm(null)} title={form?.id ? "Lead bearbeiten" : "Neuer Lead"} subtitle="Anfrage oder Verkaufschance"
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="lead-form" variant="primary">Speichern</Button></>}>
        {form && (
          <form id="lead-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Titel" className="sm:col-span-2"><Input required placeholder="z. B. HR-Software für 40 Nutzer" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
            <Field label="Firma"><Input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} /></Field>
            <Field label="Ansprechpartner"><Input value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} /></Field>
            <Field label="E-Mail"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Telefon"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
            <Field label="Volumen netto (€)"><Input inputMode="decimal" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} /></Field>
            <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={form.recurring} onChange={(e) => setForm({ ...form, recurring: e.target.checked })} />monatlich wiederkehrend</label>
            <Field label="Wahrscheinlichkeit (%)" hint="leer = nach Phase"><Input type="number" min={0} max={100} value={form.probability} onChange={(e) => setForm({ ...form, probability: e.target.value })} /></Field>
            <Field label="Erwarteter Abschluss"><Input type="date" value={form.expected_close} onChange={(e) => setForm({ ...form, expected_close: e.target.value })} /></Field>
            <Field label="Verantwortlich"><Select value={form.owner_id} onChange={(e) => setForm({ ...form, owner_id: e.target.value })}><option value="">–</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
            <Field label="Quelle"><Input placeholder="Website, Empfehlung, Messe …" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} /></Field>
            <Field label="Notizen" className="sm:col-span-2"><Textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          </form>
        )}
      </Drawer>

      <Drawer open={!!detail} onClose={() => { setDetail(null); setAddr(null); }} size="lg" title={detail?.title ?? ""} subtitle={detail ? `#${detail.number} · ${board.stages.find((x) => x.key === detail.stage)?.label}` : ""}
        footer={detail && <>
          {!detail.customer_id && !detail.offer_id && <Button variant="ghost" className="mr-auto" onClick={async () => { if (await confirm(`Lead „${detail.title}“ löschen?`)) { await run(() => api("DELETE", `/api/crm/leads/${detail.id}`), "Lead gelöscht."); setDetail(null); } }}>Löschen</Button>}
          <Button onClick={() => { setForm({ id: detail.id, title: detail.title, company: detail.company ?? "", contact_name: detail.contact_name ?? "", email: detail.email ?? "", phone: detail.phone ?? "", source: detail.source ?? "", value: detail.value.replace(".", ","), recurring: detail.recurring, probability: detail.probability?.toString() ?? "", expected_close: detail.expected_close ?? "", owner_id: detail.owner_id ?? "", notes: detail.notes ?? "" }); setDetail(null); }}>Bearbeiten</Button>
        </>}>
        {detail && (
          <div className="grid gap-5">
            <div className="flex flex-wrap gap-1.5">
              {board.stages.map((st) => <button key={st.key} type="button" onClick={() => void moveTo(detail, st.key)} className={`rounded-full px-3 py-1 text-xs font-medium ${detail.stage === st.key ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-white/5 dark:text-slate-300"}`}>{st.label}</button>)}
            </div>
            {detail.stage === "lost" && detail.lost_reason && <Alert kind="info">Verloren: {detail.lost_reason}</Alert>}
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              {[["Firma", detail.company], ["Ansprechpartner", detail.contact_name], ["E-Mail", detail.email], ["Telefon", detail.phone], ["Volumen", `${formatEuro(detail.value)}${detail.recurring ? " / Monat" : ""}`], ["Abschluss erwartet", detail.expected_close ? formatDate(detail.expected_close) : null], ["Quelle", detail.source]].map(([k, v]) => <div key={k}><dt className="text-xs text-slate-500">{k}</dt><dd>{v || "–"}</dd></div>)}
            </dl>
            <div className="flex flex-wrap gap-2 rounded-xl bg-slate-50 p-3 dark:bg-white/[0.03]">
              {detail.customer_id ? <Link className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-300" href="/kunden">Kunde angelegt →</Link>
                : addr ? (
                  <form className="grid w-full gap-2 sm:grid-cols-4" onSubmit={(e) => { e.preventDefault(); void run(() => api("POST", `/api/crm/leads/${detail.id}/customer`, { ...addr, vat_id: addr.vat_id || null, email: detail.email }), "Kunde angelegt."); setAddr(null); }}>
                    <Input aria-label="Straße" placeholder="Straße und Nr." required className="sm:col-span-2" value={addr.street} onChange={(e) => setAddr({ ...addr, street: e.target.value })} />
                    <Input aria-label="PLZ" placeholder="PLZ" required value={addr.postal_code} onChange={(e) => setAddr({ ...addr, postal_code: e.target.value })} />
                    <Input aria-label="Ort" placeholder="Ort" required value={addr.city} onChange={(e) => setAddr({ ...addr, city: e.target.value })} />
                    <Input aria-label="Land" placeholder="Land (DE)" maxLength={2} value={addr.country_code} onChange={(e) => setAddr({ ...addr, country_code: e.target.value.toUpperCase() })} />
                    <Input aria-label="USt-IdNr" placeholder="USt-IdNr (optional)" className="sm:col-span-2" value={addr.vat_id} onChange={(e) => setAddr({ ...addr, vat_id: e.target.value })} />
                    <Button type="submit" variant="primary">Kunde anlegen</Button>
                  </form>
                ) : <Button onClick={() => setAddr({ street: "", postal_code: "", city: "", country_code: "DE", vat_id: "" })} disabled={!detail.email}>{detail.email ? "Kunde anlegen" : "Kunde anlegen (E-Mail fehlt)"}</Button>}
              {detail.offer_id ? <Link className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-300" href={`/angebote/${detail.offer_id}`}>Angebot {detail.offer_number ?? ""} öffnen →</Link>
                : detail.customer_id && <Button variant="primary" onClick={() => void run(async () => { const r = await api<{ offer_id: string }>("POST", `/api/crm/leads/${detail.id}/offer`); window.location.href = `/angebote/${r.offer_id}`; }, "Angebotsentwurf erstellt.")}>Angebot erstellen</Button>}
            </div>
            <form className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_9rem_auto]" onSubmit={(e) => { e.preventDefault(); void run(() => api("POST", `/api/crm/leads/${detail.id}/activities`, { kind: act.kind, text: act.text, due_date: act.due_date || null }), "Eingetragen."); setAct({ kind: "note", text: "", due_date: "" }); }}>
              <Select aria-label="Art" value={act.kind} onChange={(e) => setAct({ ...act, kind: e.target.value })}>{["note", "call", "email", "meeting", "task"].map((k) => <option key={k} value={k}>{KIND[k]}</option>)}</Select>
              <Input aria-label="Text" required placeholder={act.kind === "task" ? "Was ist zu tun?" : "Was ist passiert?"} value={act.text} onChange={(e) => setAct({ ...act, text: e.target.value })} />
              <Input aria-label="Fällig" type="date" value={act.due_date} onChange={(e) => setAct({ ...act, due_date: e.target.value })} />
              <Button type="submit">Hinzufügen</Button>
            </form>
            <ol className="relative grid gap-3 border-l border-slate-200 pl-4 dark:border-white/10">
              {detail.activities.map((a) => (
                <li key={a.id} className="text-sm">
                  <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full bg-slate-300 dark:bg-white/30" />
                  <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500"><Badge tone={a.kind === "task" ? (a.done_at ? "green" : "amber") : "slate"}>{KIND[a.kind] ?? a.kind}</Badge>{new Date(a.created_at).toLocaleString("de-DE")} · {a.created_by}
                    {a.due_date && <span>fällig {formatDate(a.due_date)}</span>}
                    {a.kind === "task" && <button type="button" className="text-brand-600 hover:underline" onClick={() => void run(() => api("POST", `/api/crm/activities/${a.id}/done`, { done: !a.done_at }))}>{a.done_at ? "wieder offen" : "erledigt"}</button>}</span>
                  <span className={`block whitespace-pre-line ${a.done_at ? "text-slate-400 line-through" : ""}`}>{a.text}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </Drawer>
    </>
  );
}
