"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Input } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

interface SignatureRequest { id: string; title: string; signer_name: string; signer_email: string; countersigner_email: string | null; documenso_id: number | null; status: "pending" | "completed" | "rejected" | "cancelled"; last_event: string | null; created_at: string; completed_at: string | null; signed_sha256: string | null }

const STATUS: Record<SignatureRequest["status"], [string, "blue" | "green" | "red" | "slate"]> = {
  pending: ["wartet auf Unterschrift", "blue"], completed: ["unterschrieben", "green"], rejected: ["abgelehnt", "red"], cancelled: ["abgebrochen", "slate"],
};
const linkClass = "rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800";

export function SignaturePanel({ subjectType, subjectId, canRequest, defaultName, defaultEmail, onChange }: {
  subjectType: "offer" | "contract"; subjectId: string; canRequest: boolean; defaultName: string; defaultEmail: string; onChange?: () => void;
}) {
  const [items, setItems] = useState<SignatureRequest[]>([]);
  const [configured, setConfigured] = useState(true);
  const [form, setForm] = useState({ signer_name: defaultName, signer_email: defaultEmail, countersign: subjectType === "contract" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    void api<{ configured: boolean; items: SignatureRequest[] }>("GET", `/api/signatures?subject_type=${subjectType}&subject_id=${subjectId}`)
      .then((r) => { setItems(r.items); setConfigured(r.configured); });
  }, [subjectType, subjectId]);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); load(); onChange?.(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }
  const pending = items.some((i) => i.status === "pending");

  return (
    <Card className="mt-4">
      <h2 className="mb-2 text-sm font-medium text-slate-500">Digitale Unterschrift (Documenso)</h2>
      {error && <div className="mb-2"><Alert>{error}</Alert></div>}
      {items.length > 0 && (
        <ul className="mb-3 space-y-1 text-sm">
          {items.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>{s.signer_name} ({s.signer_email}){s.countersigner_email ? ` + Gegenzeichnung ${s.countersigner_email}` : ""} · {new Date(s.created_at).toLocaleDateString("de-DE")}</span>
              <span className="flex flex-wrap items-center gap-2">
                <Badge tone={STATUS[s.status][1]}>{STATUS[s.status][0]}</Badge>
                <a className={linkClass} href={`/api/signatures/${s.id}/document`} target="_blank" rel="noopener">Original</a>
                {s.status === "completed" && <a className={linkClass} href={`/api/signatures/${s.id}/document?signed=true`} target="_blank" rel="noopener">Signiert</a>}
                {s.status === "pending" && configured && <Button className="!px-2 !h-7 !py-0 !text-xs" onClick={() => act(() => api("POST", `/api/signatures/${s.id}/refresh`))} disabled={busy}>Status abgleichen</Button>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {!configured ? (
        <p className="text-sm text-slate-500">Documenso ist noch nicht verbunden: In Documenso (sign-Subdomain) ein API-Token und einen Webhook auf <code>/api/webhooks/documenso</code> anlegen und <code>DOCUMENSO_API_TOKEN</code> in der .env setzen.</p>
      ) : canRequest && !pending ? (
        <div className="grid items-end gap-2 sm:grid-cols-[1fr_1fr_auto_auto]">
          <Field label="Unterzeichner"><Input value={form.signer_name} onChange={(e) => setForm({ ...form, signer_name: e.target.value })} /></Field>
          <Field label="E-Mail"><Input type="email" value={form.signer_email} onChange={(e) => setForm({ ...form, signer_email: e.target.value })} /></Field>
          <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={form.countersign} onChange={(e) => setForm({ ...form, countersign: e.target.checked })} /> selbst gegenzeichnen</label>
          <Button variant="primary" disabled={busy || form.signer_name.trim().length < 2 || !form.signer_email.includes("@")}
            onClick={() => act(() => api("POST", "/api/signatures", { subject_type: subjectType, subject_id: subjectId, ...form }))}>Zur Unterschrift senden</Button>
        </div>
      ) : !pending && items.length === 0 ? (
        <p className="text-sm text-slate-500">{subjectType === "offer" ? "Signatur ist möglich, sobald das Angebot versendet ist." : "Signatur ist nur für Vertragsentwürfe möglich."}</p>
      ) : null}
      <p className="mt-2 text-xs text-slate-500">Nach vollständiger Unterschrift wird {subjectType === "offer" ? "das Angebot automatisch als angenommen erfasst" : "der Vertrag automatisch aktiviert"}; das signierte PDF wird mit Hash im Ledger festgeschrieben.</p>
    </Card>
  );
}
