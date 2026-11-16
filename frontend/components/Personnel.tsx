"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { TemplateDrawer } from "@/components/DocumensoPanel";
import { type Channel, type ChannelInfo, ChannelChooser, SignedCopyButton } from "@/components/SignChannels";
import { useFeedback } from "@/components/Feedback";
import { Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { api, HttpError } from "@/lib/http";
import { sendForSignature } from "@/lib/signature";

export interface ChecklistItem { id: string; position: number; title: string; description: string | null; responsible: string | null; due_date: string | null; legal: string | null; done_at: string | null; done_by: string | null }
export interface Checklist { id: string; employee_id: string; employee: string; kind: "onboarding" | "offboarding"; name: string; reference_date: string; completed_at: string | null; items: ChecklistItem[]; done: number; total: number }
interface Doc {
  id: string; category: string; category_label: string; title: string; filename: string; mime_type: string; size: number; valid_from: string | null; valid_until: string | null;
  visible_to_employee: boolean; signature_status: "pending" | "signed" | "rejected" | null;
  handoff?: { id: string; status: string; channel: string | null } | null; delete_after: string | null; notes: string | null; created_by: string; created_at: string;
}
interface ContractTemplate { id: string; name: string; category: string; body: string }

const dl = (iso: string | null) => (iso ? new Date(iso.slice(0, 10) + "T12:00:00").toLocaleDateString("de-DE") : "–");
const KIND = { onboarding: "Onboarding", offboarding: "Offboarding" } as const;
const today = () => new Date().toISOString().slice(0, 10);

/** Checklisten-Karte: Fortschritt, Punkte mit Frist, Verantwortlichen und Rechtsgrundlage zum Abhaken. */
export function ChecklistCard({ c, onChange, showEmployee = false }: { c: Checklist; onChange: () => void; showEmployee?: boolean }) {
  const { toast } = useFeedback();
  const [adding, setAdding] = useState("");
  const toggle = (i: ChecklistItem) => api("POST", `/api/personnel/checklist-items/${i.id}`, { done: !i.done_at }).then(onChange)
    .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!adding.trim()) return;
    api("POST", `/api/personnel/checklists/${c.id}/items`, { title: adding.trim() }).then(() => { setAdding(""); onChange(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  const pct = c.total ? Math.round((c.done / c.total) * 100) : 0;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 px-5 pt-4">
        <Badge tone={c.kind === "onboarding" ? "green" : "amber"}>{KIND[c.kind]}</Badge>
        <h3 className="min-w-0 flex-1 truncate font-semibold">{showEmployee ? c.employee : c.name}</h3>
        <span className="text-xs text-slate-500">{c.kind === "onboarding" ? "Eintritt" : "Austritt"} {dl(c.reference_date)}</span>
        {c.completed_at && <Badge tone="green">erledigt</Badge>}
      </div>
      <div className="mx-5 mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"><div className="h-full bg-brand-500 transition-all" style={{ width: `${pct}%` }} /></div>
      <p className="px-5 pt-1 text-xs text-slate-500">{c.done} von {c.total} erledigt</p>
      <ul className="mt-2 divide-y divide-slate-100 dark:divide-slate-800">
        {c.items.map((i) => {
          const overdue = !i.done_at && i.due_date && i.due_date < today();
          return (
            <li key={i.id} className="flex items-start gap-3 px-5 py-2.5 text-sm">
              <input type="checkbox" className="mt-0.5 size-4 accent-teal-600" checked={!!i.done_at} onChange={() => void toggle(i)} aria-label={i.title} />
              <div className="min-w-0 flex-1">
                <p className={i.done_at ? "text-slate-400 line-through" : "font-medium text-slate-800 dark:text-slate-200"}>{i.title}</p>
                <p className="text-xs text-slate-500">
                  {i.responsible && <>{i.responsible} · </>}
                  {i.due_date && <span className={overdue ? "font-semibold text-rose-600" : ""}>fällig {dl(i.due_date)}{overdue ? " (überfällig)" : ""}</span>}
                  {i.done_at && <> · erledigt {dl(i.done_at)}{i.done_by ? ` von ${i.done_by}` : ""}</>}
                </p>
                {i.legal && <p className="mt-0.5 text-[11px] text-slate-400">{i.legal}</p>}
              </div>
            </li>
          );
        })}
      </ul>
      <form onSubmit={add} className="flex gap-2 border-t border-slate-100 px-5 py-3 dark:border-slate-800">
        <Input placeholder="Weiteren Punkt hinzufügen" value={adding} onChange={(e) => setAdding(e.target.value)} />
        <Button type="submit" disabled={!adding.trim()}>Hinzufügen</Button>
      </form>
    </Card>
  );
}

export function EmployeeChecklists({ employeeId, entryDate, exitDate }: { employeeId: string; entryDate: string; exitDate: string | null }) {
  const [lists, setLists] = useState<Checklist[] | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<Checklist[]>("GET", `/api/personnel/${employeeId}/checklists`).then(setLists); }, [employeeId]);
  useEffect(load, [load]);
  const start = (kind: "onboarding" | "offboarding") => {
    api("POST", `/api/personnel/${employeeId}/checklists`, { kind, reference_date: kind === "onboarding" ? entryDate : exitDate ?? today() })
      .then(() => { toast(`${KIND[kind]} gestartet.`); load(); }).catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  };
  if (!lists) return <Skeleton className="h-64" />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => start("onboarding")}>+ Onboarding</Button>
        <Button onClick={() => start("offboarding")}>+ Offboarding{exitDate ? ` (Austritt ${dl(exitDate)})` : ""}</Button>
      </div>
      {lists.length === 0 ? <Card className="p-5"><Empty>Noch keine Checkliste.</Empty></Card> : lists.map((c) => <ChecklistCard key={c.id} c={c} onChange={load} />)}
    </div>
  );
}

/** Digitale Personalakte: Dokumente mit Kategorie, Sichtbarkeit für den Mitarbeiter, Signatur und Löschfrist. */
export function PersonnelFile({ employeeId, email, employeeName }: { employeeId: string; email: string | null; employeeName?: string }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [cats, setCats] = useState<Record<string, string>>({});
  const [templates, setTemplates] = useState<ContractTemplate[]>([]);
  const [upload, setUpload] = useState<{ category: string; title: string; visible: boolean; valid_from: string; valid_until: string; notes: string } | null>(null);
  const [edit, setEdit] = useState<Doc | null>(null);
  const [tplOpen, setTplOpen] = useState(false);
  const [choose, setChoose] = useState<{ doc: Doc; info: ChannelInfo } | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const { toast, confirm, prompt } = useFeedback();
  const load = useCallback(() => {
    void api<{ documents: Doc[]; categories: Record<string, string> }>("GET", `/api/personnel/${employeeId}/documents`).then((r) => { setDocs(r.documents); setCats(r.categories); });
  }, [employeeId]);
  useEffect(load, [load]);
  useEffect(() => { void api<{ templates: ContractTemplate[] }>("GET", "/api/personnel/contract-templates").then((r) => setTemplates(r.templates)); }, []);

  const doUpload = async (e: FormEvent) => {
    e.preventDefault();
    const f = fileRef.current?.files?.[0];
    if (!upload || !f) return;
    const fd = new FormData();
    fd.append("file", f); fd.append("category", upload.category); fd.append("title", upload.title || f.name);
    fd.append("visible_to_employee", String(upload.visible));
    if (upload.valid_from) fd.append("valid_from", upload.valid_from);
    if (upload.valid_until) fd.append("valid_until", upload.valid_until);
    if (upload.notes) fd.append("notes", upload.notes);
    const res = await fetch(`/api/personnel/${employeeId}/documents`, { method: "POST", body: fd });
    if (!res.ok) { toast(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? "Upload fehlgeschlagen", "error"); return; }
    toast("Abgelegt."); setUpload(null); load();
  };
  const generate = (t: ContractTemplate) => api<{ missing: string[] }>("POST", `/api/personnel/${employeeId}/contracts`, { template_id: t.id })
    .then((r) => { toast(r.missing.length ? `Erstellt – fehlende Angaben: ${r.missing.join(", ")}` : `${t.name} erstellt.`, r.missing.length ? "error" : "success"); load(); })
    .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  const sign = async (d: Doc) => {
    const info = await api<ChannelInfo>("GET", `/api/personnel/documents/${d.id}/form`).catch(() => null);
    if (info) setChoose({ doc: d, info });
  };
  const onChoose = (channel: Channel, note: string | null) => {
    if (!choose) return;
    const d = choose.doc;
    if ((channel === "documenso" || channel === "qes" || channel === "email") && !email) { toast("Für diesen Weg braucht der Mitarbeiter eine E-Mail-Adresse.", "error"); return; }
    setBusy(true);
    const done = (msg: string) => { toast(msg); setChoose(null); load(); };
    const req = channel === "documenso"
      ? sendForSignature((ack) => api("POST", `/api/personnel/documents/${d.id}/sign`, { countersign: true, acknowledge_form: ack }), confirm).then((r) => { if (r === "sent") done("Signaturanfrage über Documenso versendet."); })
      : api("POST", `/api/personnel/documents/${d.id}/handoff`, { channel, note }).then(() => {
        if (channel === "paper") window.open(`/api/personnel/documents/${d.id}/file`, "_blank", "noopener");
        done(channel === "qes" ? "Qualifizierte Signatur angefragt." : channel === "email" ? `PDF an ${email} gesendet – nach Eingang den Scan hochladen.` : "PDF geöffnet – nach der Unterschrift den Scan hochladen, Original in die Papierakte.");
      });
    req.catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error")).finally(() => setBusy(false));
  };
  const uploadScan = async (d: Doc, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/personnel/documents/${d.id}/signed-copy`, { method: "POST", body: fd });
    if (!res.ok) { toast(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? "Hochladen fehlgeschlagen", "error"); return; }
    toast("Unterschriebenes Exemplar abgelegt – Original aufbewahren."); load();
  };
  const remove = async (d: Doc) => {
    const reason = await prompt({ title: `„${d.title}“ endgültig löschen?`, message: "Der Inhalt wird unwiederbringlich gelöscht (DSGVO). Titel, Prüfsumme und Löschgrund bleiben als Nachweis.",
      confirmLabel: "Löschen", tone: "danger", input: { label: "Grund", minLength: 3, placeholder: "z. B. Löschfrist abgelaufen" } });
    if (!reason) return;
    api("POST", `/api/personnel/documents/${d.id}/delete`, { reason }).then(() => { toast("Gelöscht."); load(); })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  };
  const saveEdit = (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    api("PUT", `/api/personnel/documents/${edit.id}`, { title: edit.title, category: edit.category, visible_to_employee: edit.visible_to_employee,
      valid_from: edit.valid_from || null, valid_until: edit.valid_until || null, delete_after: edit.delete_after || null, notes: edit.notes || null })
      .then(() => { toast("Gespeichert."); setEdit(null); load(); }).catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };

  if (!docs) return <Skeleton className="h-64" />;
  const now = today();
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => setUpload({ category: "contract", title: "", visible: false, valid_from: "", valid_until: "", notes: "" })}>Dokument hochladen</Button>
        {templates.map((t) => <Button key={t.id} onClick={() => void generate(t)}>Aus Vorlage: {t.name}</Button>)}
        <Button onClick={() => setTplOpen(true)}>Aus Documenso-Vorlage</Button>
      </div>
      <Card>
        <CardHeader title="Personalakte" description="Nur für Personen mit Recht Lohn & Personal. „Für Mitarbeiter sichtbar“ erscheint im Portal. Löschfrist: 3 Jahre nach Austritt (anpassbar)." />
        {docs.length === 0 ? <div className="p-5"><Empty>Noch keine Dokumente.</Empty></div> : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {docs.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <a href={`/api/personnel/documents/${d.id}/file`} target="_blank" rel="noreferrer" className="font-medium text-slate-900 hover:underline dark:text-slate-100">{d.title}</a>
                  <p className="text-xs text-slate-500">{d.category_label} · {dl(d.created_at)}{d.valid_until ? ` · gültig bis ${dl(d.valid_until)}` : ""}{d.delete_after ? ` · löschen ab ${dl(d.delete_after)}` : ""}</p>
                </div>
                {d.visible_to_employee && <Badge tone="blue">im Portal</Badge>}
                {d.signature_status === "pending" && <Badge tone="amber">Signatur läuft</Badge>}
                {d.signature_status === "signed" && <a href={`/api/personnel/documents/${d.id}/file?signed=true`} target="_blank" rel="noreferrer"><Badge tone="green">signiert ↓</Badge></a>}
                {d.signature_status === "rejected" && <Badge tone="red">abgelehnt</Badge>}
                {d.handoff?.status === "awaiting_paper" && <Badge tone="amber">wartet auf Original</Badge>}
                {d.handoff?.status === "pending" && <Badge tone="amber">qualifizierte Signatur läuft</Badge>}
                {d.delete_after && d.delete_after < now && <Badge tone="red">Löschfrist abgelaufen</Badge>}
                <div className="flex gap-1">
                  {d.mime_type === "application/pdf" && !d.signature_status && d.handoff?.status !== "awaiting_paper" && d.handoff?.status !== "pending" && <Button className="!px-2 !py-1 text-xs" onClick={() => void sign(d)}>Unterschrift einholen</Button>}
                  {d.handoff?.status === "awaiting_paper" && <SignedCopyButton onUpload={(f) => void uploadScan(d, f)} />}
                  <Button className="!px-2 !py-1 text-xs" onClick={() => setEdit(d)}>Bearbeiten</Button>
                  <Button className="!px-2 !py-1 text-xs" onClick={() => void remove(d)}>Löschen</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ChannelChooser open={!!choose} onClose={() => setChoose(null)} info={choose?.info ?? null} title={choose?.doc.title ?? ""} busy={busy} onChoose={onChoose} />
      <TemplateDrawer open={tplOpen} onClose={() => setTplOpen(false)} onSent={load} listPath="/api/personnel/documenso-templates"
        usePath={() => `/api/personnel/${employeeId}/documenso-template`} defaults={{ name: employeeName ?? "", email: email ?? "" }}
        title="Vertrag aus Documenso-Vorlage" />
      <Drawer open={!!upload} onClose={() => setUpload(null)} title="Dokument in die Personalakte"
        footer={<><Button onClick={() => setUpload(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="pf-upload">Hochladen</Button></>}>
        {upload && (
          <form id="pf-upload" onSubmit={(e) => void doUpload(e)} className="grid gap-4">
            <Field label="Datei" hint="PDF, PNG, JPG, TXT oder DOCX, max. 20 MB"><input ref={fileRef} type="file" required accept=".pdf,.png,.jpg,.jpeg,.txt,.docx" className="text-sm" /></Field>
            <Field label="Kategorie"><Select value={upload.category} onChange={(e) => setUpload({ ...upload, category: e.target.value })}>{Object.entries(cats).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
            <Field label="Titel" hint="leer = Dateiname"><Input value={upload.title} onChange={(e) => setUpload({ ...upload, title: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="gültig ab"><Input type="date" value={upload.valid_from} onChange={(e) => setUpload({ ...upload, valid_from: e.target.value })} /></Field>
              <Field label="gültig bis" hint="z. B. Aufenthaltstitel"><Input type="date" value={upload.valid_until} onChange={(e) => setUpload({ ...upload, valid_until: e.target.value })} /></Field>
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={upload.visible} onChange={(e) => setUpload({ ...upload, visible: e.target.checked })} /> für den Mitarbeiter im Portal sichtbar</label>
            <Field label="Notiz"><Textarea rows={2} value={upload.notes} onChange={(e) => setUpload({ ...upload, notes: e.target.value })} /></Field>
            {upload.category === "health" && <p className="text-xs text-amber-700 dark:text-amber-400">Gesundheitsdaten (Art. 9 DSGVO): nur ablegen, was rechtlich nötig ist – z. B. keine Diagnosen.</p>}
          </form>
        )}
      </Drawer>
      <Drawer open={!!edit} onClose={() => setEdit(null)} title="Dokument bearbeiten"
        footer={<><Button onClick={() => setEdit(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="pf-edit">Speichern</Button></>}>
        {edit && (
          <form id="pf-edit" onSubmit={saveEdit} className="grid gap-4">
            <Field label="Titel"><Input required value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></Field>
            <Field label="Kategorie"><Select value={edit.category} onChange={(e) => setEdit({ ...edit, category: e.target.value })}>{Object.entries(cats).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="gültig ab"><Input type="date" value={edit.valid_from ?? ""} onChange={(e) => setEdit({ ...edit, valid_from: e.target.value })} /></Field>
              <Field label="gültig bis"><Input type="date" value={edit.valid_until ?? ""} onChange={(e) => setEdit({ ...edit, valid_until: e.target.value })} /></Field>
            </div>
            <Field label="Löschen ab" hint="Art. 17 DSGVO – danach erscheint ein Hinweis unter „Zu erledigen“"><Input type="date" value={edit.delete_after ?? ""} onChange={(e) => setEdit({ ...edit, delete_after: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={edit.visible_to_employee} onChange={(e) => setEdit({ ...edit, visible_to_employee: e.target.checked })} /> für den Mitarbeiter sichtbar</label>
            <Field label="Notiz"><Textarea rows={3} value={edit.notes ?? ""} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
          </form>
        )}
      </Drawer>
    </div>
  );
}

/** Eigene Dokumente (App „Mein Konto“ bzw. Portal). */
export function MyDocuments({ base, fetcher }: { base: string; fetcher: <T>(m: "GET", p: string) => Promise<T> }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  useEffect(() => { fetcher<Doc[]>("GET", `${base}/documents`).then(setDocs).catch(() => setDocs([])); }, [base, fetcher]);
  if (!docs) return <Skeleton className="h-32" />;
  return (
    <Card>
      <CardHeader title="Meine Dokumente" description="Vom Arbeitgeber freigegebene Unterlagen, z. B. Arbeitsvertrag, Zeugnisse, Bescheinigungen." />
      {docs.length === 0 ? <div className="p-5"><Empty>Keine freigegebenen Dokumente.</Empty></div> : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {docs.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
              <div className="min-w-0 flex-1"><p className="font-medium">{d.title}</p><p className="text-xs text-slate-500">{d.category_label} · {dl(d.created_at)}</p></div>
              <a className="text-xs font-medium text-brand-600 hover:underline" href={`${base}/documents/${d.id}/file${d.signature_status === "signed" ? "?signed=true" : ""}`} target="_blank" rel="noreferrer">
                {d.signature_status === "signed" ? "Signiertes PDF" : "Öffnen"}</a>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
