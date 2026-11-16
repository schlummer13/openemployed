"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { TemplateDrawer } from "@/components/DocumensoPanel";
import { useFeedback } from "@/components/Feedback";
import { type Channel, ChannelChooser, SignedCopyButton } from "@/components/SignChannels";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { api, HttpError } from "@/lib/http";
import { sendForSignature } from "@/lib/signature";

interface Kind { key: string; label: string; level: "ok" | "warn" | "block"; law: string }
interface Doc {
  source?: "upload" | "documenso" | "template"; documenso_id?: number | null; documenso_status?: string | null;
  id: string; title: string; kind: string; kind_label: string; counterparty: string; signer_name: string; signer_email: string; status: "draft" | "pending" | "awaiting_paper" | "signed" | "rejected";
  form_level: "ok" | "warn" | "block"; channel?: "documenso" | "qes" | "paper" | "email" | null; mailed_to?: string | null; form_message: string; form_acknowledged_by: string | null; archive_document_id: string | null; created_at: string; completed_at: string | null;
}
interface Check { kind: string; level: "ok" | "warn" | "block"; label: string; message: string }

const STATUS: Record<Doc["status"], [string, "slate" | "amber" | "green" | "red"]> = {
  draft: ["Entwurf", "slate"], pending: ["Signatur läuft", "amber"], awaiting_paper: ["wartet auf unterschriebenes Original", "amber"],
  signed: ["signiert · archiviert", "green"], rejected: ["abgelehnt", "red"],
};
const LEVEL_TONE = { ok: "green", warn: "amber", block: "red" } as const;
const LEVEL_TEXT = { ok: "digital wirksam", warn: "Schriftform – qualifiziert oder Papier", block: "nur Papier (Original)" } as const;
const CHANNEL: Record<string, string> = { documenso: "Documenso", qes: "qualifiziert", paper: "Papier", email: "per E-Mail" };
type ListData = { documents: Doc[]; kinds: Kind[]; configured: boolean; team_url?: string | null; qes: { configured: boolean; label: string }; mail: boolean };

/** Beliebige Verträge (Lieferanten, Vermieter, NDA, Partner) per Documenso signieren – mit Formprüfung und Archivierung. */
export function SignDocuments() {
  const [data, setData] = useState<ListData | null>(null);
  const [choose, setChoose] = useState<Doc | null>(null);
  const [busy, setBusy] = useState(false);
  const [tplOpen, setTplOpen] = useState(false);
  const [form, setForm] = useState<{ title: string; kind: string; counterparty: string; signer_name: string; signer_email: string; notes: string } | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<ListData>("GET", "/api/signatures/documents").then(setData); }, []);
  useEffect(load, [load]);
  useEffect(() => {
    if (!form) { setCheck(null); return; }
    const t = setTimeout(() => { void api<Check>("POST", "/api/signatures/documents/check", { kind: form.kind, title: form.title }).then(setCheck); }, 250);
    return () => clearTimeout(t);
  }, [form?.kind, form?.title]); // eslint-disable-line react-hooks/exhaustive-deps

  const create = async (e: FormEvent) => {
    e.preventDefault();
    const f = fileRef.current?.files?.[0];
    if (!form || !f) return;
    const fd = new FormData();
    Object.entries(form).forEach(([k, v]) => { if (v) fd.append(k, v); });
    fd.append("file", f);
    const res = await fetch("/api/signatures/documents", { method: "POST", body: fd });
    const body = await res.json().catch(() => null) as (Doc & { detail?: string }) | null;
    if (!res.ok) { toast(body?.detail ?? "Anlegen fehlgeschlagen", "error"); return; }
    setForm(null); load();
    if (body) setChoose(body);
  };
  const onChoose = (channel: Channel, note: string | null) => {
    const d = choose;
    if (!d) return;
    if (channel === "documenso") { setChoose(null); void send(d); return; }
    setBusy(true);
    const done = (msg: string) => { toast(msg); setChoose(null); load(); };
    const req = channel === "qes" ? api<{ signing_url?: string | null }>("POST", `/api/signatures/documents/${d.id}/qes`).then(() => done("Qualifizierte Signatur angefragt – die Personen erhalten eine E-Mail des Anbieters."))
      : channel === "email" ? api("POST", `/api/signatures/documents/${d.id}/mail`, { note }).then(() => done(`PDF an ${d.signer_email} gesendet – nach Eingang den Scan hochladen.`))
      : api("POST", `/api/signatures/documents/${d.id}/paper`, { note }).then(() => { window.open(`/api/signatures/documents/${d.id}/file`, "_blank", "noopener"); done("PDF geöffnet – ausdrucken, unterschreiben lassen, Scan hier hochladen, Original aufbewahren."); });
    req.catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error")).finally(() => setBusy(false));
  };
  const uploadScan = async (d: Doc, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/signatures/documents/${d.id}/signed-copy`, { method: "POST", body: fd });
    if (!res.ok) { toast(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? "Hochladen fehlgeschlagen", "error"); return; }
    toast("Unterschriebenes Exemplar archiviert – Original bitte aufbewahren."); load();
  };
  const send = (d: Doc) => sendForSignature((ack) => api("POST", `/api/signatures/documents/${d.id}/send`, { countersign: true, acknowledge_form: ack }), confirm)
    .then((r) => { if (r === "sent") { toast("Signaturanfrage versendet."); load(); } })
    .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Versand fehlgeschlagen", "error"));

  if (!data) return <Skeleton className="h-64" />;
  return (
    <div className="space-y-4">
      {!data.configured && <Alert kind="warning">Documenso ist nicht verbunden – Verträge lassen sich trotzdem ausdrucken, per E-Mail senden{data.qes.configured ? " oder qualifiziert signieren" : ""}.</Alert>}
      <Card>
        <CardHeader title="Sonstige Verträge digital signieren" description="Für Lieferanten, Dienstleister, Vermieter und Partner: PDF hochladen, OpenEmployed prüft die Formvorschrift, Documenso holt die Unterschriften, das signierte PDF landet revisionssicher im Archiv."
          actions={<div className="flex flex-wrap gap-2">
            {data.team_url && <a className="inline-flex h-9 items-center rounded-lg border border-slate-200 px-3 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-white/5" href={`${data.team_url}/documents`} target="_blank" rel="noreferrer">In Documenso anlegen/bearbeiten</a>}
            {data.configured && <Button onClick={() => setTplOpen(true)}>Aus Documenso-Vorlage</Button>}
            <Button variant="primary" onClick={() => setForm({ title: "", kind: "general", counterparty: "", signer_name: "", signer_email: "", notes: "" })}>+ PDF zur Signatur</Button>
          </div>} />
        {data.documents.length === 0 ? <div className="px-5 pb-5"><Empty>Noch keine Verträge.</Empty></div> : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.documents.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <a className="font-medium hover:underline" href={`/api/signatures/documents/${d.id}/file${d.status === "signed" ? "?signed=true" : ""}`} target="_blank" rel="noreferrer">{d.title}</a>
                  <p className="text-xs text-slate-500">{d.counterparty} · {d.signer_name} ({d.signer_email}) · {d.kind_label}</p>
                  {d.form_level !== "ok" && <p className={`mt-0.5 text-xs ${d.form_level === "block" ? "text-rose-700 dark:text-rose-400" : "text-amber-700 dark:text-amber-400"}`}>{d.form_message}{d.form_acknowledged_by ? ` Bestätigt von ${d.form_acknowledged_by}.` : ""}</p>}
                  {d.channel && d.channel !== "documenso" && <p className="mt-0.5 text-xs text-slate-500">Weg: {CHANNEL[d.channel]}{d.mailed_to ? ` an ${d.mailed_to}` : ""}</p>}
                </div>
                {d.source === "documenso" && <Badge tone="blue">in Documenso angelegt</Badge>}
                {d.source === "template" && <Badge tone="blue">aus Documenso-Vorlage</Badge>}
                <Badge tone={STATUS[d.status][1]}>{STATUS[d.status][0]}</Badge>
                {d.source === "documenso" && d.status === "draft" && data.team_url && <a className="text-xs font-medium text-brand-600 hover:underline" href={`${data.team_url}/documents`} target="_blank" rel="noreferrer">in Documenso versenden</a>}
                {(d.status === "draft" || d.status === "rejected") && d.source !== "documenso" && d.source !== "template" && <Button className="!px-2 !py-1 text-xs" onClick={() => setChoose(d)}>Unterschrift einholen</Button>}
                {d.status === "awaiting_paper" && <SignedCopyButton onUpload={(f) => void uploadScan(d, f)} />}
                {d.archive_document_id && <a className="text-xs font-medium text-brand-600 hover:underline" href={`/api/archive/${d.archive_document_id}/file`} target="_blank" rel="noreferrer">im Archiv</a>}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card>
        <CardHeader title="Welche Verträge gehen digital?" description="Documenso erfüllt Textform und Formfreiheit, nicht die Schriftform (§ 126 BGB). Diese ersetzt nur die eigenhändige oder qualifizierte elektronische Unterschrift." />
        <ul className="grid gap-x-6 gap-y-1.5 px-5 pb-5 text-xs sm:grid-cols-2">
          {data.kinds.map((k) => <li key={k.key} className="flex items-start gap-2"><Badge tone={LEVEL_TONE[k.level]}>{LEVEL_TEXT[k.level]}</Badge><span><strong className="font-medium">{k.label}</strong> <span className="text-slate-500">– {k.law}</span></span></li>)}
        </ul>
      </Card>

      <ChannelChooser open={!!choose} onClose={() => setChoose(null)} busy={busy} title={choose?.title ?? ""} onChoose={onChoose}
        info={choose ? { level: choose.form_level, message: choose.form_message, documenso: data.configured, mail: data.mail, qes: data.qes } : null} />
      <TemplateDrawer open={tplOpen} onClose={() => setTplOpen(false)} onSent={load} listPath="/api/signatures/templates"
        usePath={(id) => `/api/signatures/templates/${id}/use`} />
      <Drawer open={!!form} onClose={() => setForm(null)} title="Vertrag zur Signatur" subtitle="PDF ohne Kennwortschutz; eine Signaturseite wird angehängt"
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="sign-doc">Anlegen</Button></>}>
        {form && (
          <form id="sign-doc" onSubmit={(e) => void create(e)} className="grid gap-4 sm:grid-cols-2">
            <Field label="Vertrag (PDF)" className="sm:col-span-2"><input ref={fileRef} type="file" required accept="application/pdf,.pdf" className="text-sm" /></Field>
            <Field label="Titel" className="sm:col-span-2"><Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="z. B. Rahmenvertrag Hosting 2027" /></Field>
            <Field label="Vertragsart" className="sm:col-span-2"><Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {data.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}{k.level === "block" ? " – nur Papier" : k.level === "warn" ? " – Schriftform" : ""}</option>)}</Select></Field>
            {check && <div className="sm:col-span-2"><Alert kind={check.level === "ok" ? "success" : check.level === "warn" ? "warning" : "error"}>{check.level === "ok" ? `Digital wirksam – ${check.message}` : check.message}</Alert></div>}
            <Field label="Vertragspartner (Firma/Person)" className="sm:col-span-2"><Input required value={form.counterparty} onChange={(e) => setForm({ ...form, counterparty: e.target.value })} /></Field>
            <Field label="Unterzeichner/in"><Input required value={form.signer_name} onChange={(e) => setForm({ ...form, signer_name: e.target.value })} /></Field>
            <Field label="E-Mail"><Input type="email" required value={form.signer_email} onChange={(e) => setForm({ ...form, signer_email: e.target.value })} /></Field>
            <Field label="Notiz" className="sm:col-span-2"><Textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          </form>
        )}
      </Drawer>
    </div>
  );
}
