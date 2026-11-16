"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useUser } from "@/components/AuthGate";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Field, Input, Select, Skeleton } from "@/components/ui";
import { api, HttpError } from "@/lib/http";
import { sendForSignature } from "@/lib/signature";

interface Status { connected: boolean; email?: string; team?: string; team_url?: string | null; public_url?: string | null; webhook?: boolean; error?: string | null; manual?: boolean; checked_at?: string }
export interface Template { id: number; title: string; recipients: { id: number; name: string | null; email: string | null; role: string; signing_order: number | null }[] }

/** Verbindung zu Documenso (Systemeinstellungen): Status, Documenso öffnen, eigenes Passwort festlegen, neu verbinden, abgleichen. */
export function DocumensoPanel() {
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<Status>("GET", "/api/admin/documenso").then(setSt); }, []);
  useEffect(load, [load]);
  const act = (what: string, fn: () => Promise<void>) => { setBusy(what); fn().catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error")).finally(() => setBusy(null)); };
  if (!st) return <Skeleton className="h-40" />;
  return (
    <Card>
      <CardHeader title="Digitale Signatur (Documenso)" description="Wird beim Start automatisch verbunden: Konto der Geschäftsführung, Team, API-Zugang und Webhook. Verträge, die ihr im Team in Documenso anlegt oder bearbeitet, erkennt OpenEmployed sofort."
        actions={st.connected ? <Badge tone="green">verbunden</Badge> : <Badge tone="amber">nicht verbunden</Badge>} />
      <div className="space-y-3 px-5 pb-5 text-sm">
        {st.connected ? (
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {st.team && <div><dt className="text-xs text-slate-500">Team</dt><dd>{st.team}</dd></div>}
            {st.email && <div><dt className="text-xs text-slate-500">Konto</dt><dd>{st.email}</dd></div>}
            <div><dt className="text-xs text-slate-500">Rückmeldung (Webhook)</dt><dd>{st.webhook ? "eingerichtet" : st.manual ? "manuell einrichten" : "fehlt (DOCUMENSO_WEBHOOK_SECRET)"}</dd></div>
            {st.checked_at && <div><dt className="text-xs text-slate-500">geprüft</dt><dd>{new Date(st.checked_at).toLocaleString("de-DE")}</dd></div>}
          </dl>
        ) : <Alert kind="warning">{st.error ?? "Noch nicht verbunden."} OpenEmployed versucht es beim Start automatisch erneut.</Alert>}
        <div className="flex flex-wrap gap-2">
          {st.team_url && <a className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700" href={st.team_url} target="_blank" rel="noreferrer">Documenso öffnen</a>}
          {st.connected && st.email && <Button disabled={!!busy} onClick={() => act("pw", async () => {
            const r = await api<{ url: string; valid_minutes: number }>("POST", "/api/admin/documenso/password-link");
            window.open(r.url, "_blank", "noopener"); toast(`Link geöffnet – ${r.valid_minutes} Minuten gültig. Passwort in Documenso selbst festlegen.`);
          })}>Eigenes Documenso-Passwort festlegen</Button>}
          <Button disabled={!!busy} onClick={() => act("connect", async () => { await api("POST", "/api/admin/documenso/connect"); toast("Verbindung geprüft."); load(); })}>{busy === "connect" ? "Verbindet …" : "Verbindung prüfen"}</Button>
          {st.connected && <Button disabled={!!busy} onClick={() => act("sync", async () => {
            const r = await api<Record<string, number>>("POST", "/api/signatures/sync");
            toast(`Abgleich: ${r.imported} neu, ${r.updated} aktualisiert, ${r.archived} archiviert.`);
          })}>{busy === "sync" ? "Gleicht ab …" : "Jetzt abgleichen"}</Button>}
        </div>
        <p className="text-xs text-slate-500">Weitere Personen ladet ihr in Documenso unter Organisation → Mitglieder ins Team ein. Nur Dokumente und Vorlagen im Team sind für OpenEmployed sichtbar (nicht im persönlichen Bereich anderer Konten).</p>
      </div>
    </Card>
  );
}

/** Dokument aus einer Documenso-Vorlage erzeugen und versenden (Empfänger je Vorlagenrolle). */
export function TemplateDrawer({ open, onClose, onSent, listPath, usePath, defaults, title: drawerTitle }: {
  open: boolean; onClose: () => void; onSent: () => void; listPath: string; usePath: (templateId: number) => string;
  defaults?: { name: string; email: string }; title?: string;
}) {
  const me = useUser();
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [tid, setTid] = useState<number | null>(null);
  const [docTitle, setDocTitle] = useState("");
  const [rec, setRec] = useState<Record<number, { name: string; email: string }>>({});
  const [error, setError] = useState<string | null>(null);
  const { toast, confirm } = useFeedback();
  useEffect(() => {
    if (!open) return;
    setError(null);
    api<Template[]>("GET", listPath).then((t) => { setTemplates(t); if (t[0]) choose(t[0]); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Vorlagen nicht erreichbar"));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const choose = (t: Template) => {
    setTid(t.id); setDocTitle(t.title);
    const signers = [...t.recipients].sort((a, b) => (a.signing_order ?? 0) - (b.signing_order ?? 0));
    const next: Record<number, { name: string; email: string }> = {};
    signers.forEach((r, i) => {
      const fallback = i === 0 && defaults ? defaults : i === 1 || (i === 0 && !defaults) ? { name: me?.name ?? "", email: me?.email ?? "" } : { name: "", email: "" };
      next[r.id] = { name: r.name || fallback.name, email: r.email || fallback.email };
    });
    setRec(next);
  };
  const tpl = templates?.find((t) => t.id === tid);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!tpl) return;
    const recipients = tpl.recipients.map((r) => ({ id: r.id, email: rec[r.id]?.email ?? "", name: rec[r.id]?.name ?? "" })).filter((r) => r.email);
    sendForSignature((ack) => api("POST", usePath(tpl.id), { template_id: tpl.id, title: docTitle, recipients, acknowledge_form: ack }), confirm)
      .then((r) => { if (r === "sent") { toast("Aus der Vorlage erzeugt und versendet."); onSent(); onClose(); } })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"));
  };
  return (
    <Drawer open={open} onClose={onClose} title={drawerTitle ?? "Aus Documenso-Vorlage"} subtitle="Vorlagen gestaltet ihr in Documenso (Felder, Unterschriften); hier wählt ihr Empfänger und versendet."
      footer={<><Button onClick={onClose}>Abbrechen</Button><Button variant="primary" type="submit" form="tpl-use" disabled={!tpl}>Erzeugen und versenden</Button></>}>
      {error ? <Alert>{error}</Alert> : !templates ? <Skeleton className="h-40" /> : templates.length === 0 ? (
        <Alert kind="info">Noch keine Vorlagen im Team. In Documenso unter „Vorlagen“ eine anlegen – sie erscheint hier automatisch.</Alert>
      ) : (
        <form id="tpl-use" onSubmit={submit} className="grid gap-4">
          <Field label="Vorlage"><Select value={tid ?? ""} onChange={(e) => { const t = templates.find((x) => x.id === Number(e.target.value)); if (t) choose(t); }}>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</Select></Field>
          <Field label="Titel des Dokuments"><Input required value={docTitle} onChange={(e) => setDocTitle(e.target.value)} /></Field>
          {tpl?.recipients.map((r, i) => (
            <div key={r.id} className="grid gap-3 rounded-xl bg-slate-50 p-3 sm:grid-cols-2 dark:bg-white/[0.03]">
              <p className="text-xs font-medium text-slate-500 sm:col-span-2">Empfänger {i + 1}{r.name ? ` · Rolle „${r.name}“` : ""} ({r.role === "SIGNER" ? "unterschreibt" : r.role === "CC" ? "Kopie" : r.role})</p>
              <Field label="Name"><Input value={rec[r.id]?.name ?? ""} onChange={(e) => setRec({ ...rec, [r.id]: { ...(rec[r.id] ?? { email: "" }), name: e.target.value } })} /></Field>
              <Field label="E-Mail"><Input type="email" required={r.role === "SIGNER"} value={rec[r.id]?.email ?? ""} onChange={(e) => setRec({ ...rec, [r.id]: { ...(rec[r.id] ?? { name: "" }), email: e.target.value } })} /></Field>
            </div>
          ))}
        </form>
      )}
    </Drawer>
  );
}
