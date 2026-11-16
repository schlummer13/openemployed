"use client";

import { useFeedback } from "@/components/Feedback";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Select } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface Termination {
  id: string; number: string; kind: "termination" | "withdrawal"; channel: string; termination_type: string; contract_id: string | null;
  customer_id: string | null; name: string; email: string; customer_number: string | null; contract_reference: string | null;
  requested_date: string | null; reason: string | null; received_at: string; confirmation_sent_at: string | null; status: string;
  effective_date: string | null; processing_note: string | null; contract_number: string | null;
}

export function TerminationInbox({ contracts, onChange }: { contracts: { id: string; number: string; title: string; customer_name: string; status: string }[]; onChange: () => void }) {
  const fb = useFeedback();
  const [items, setItems] = useState<Termination[]>([]);
  const [assign, setAssign] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { void api<Termination[]>("GET", "/api/terminations").then(setItems); }, []);
  useEffect(load, [load]);
  const open = items.filter((t) => t.status === "received");
  if (items.length === 0) return null;

  async function process(t: Termination, accept: boolean) {
    setError(null);
    try {
      const note = accept ? null : await fb.prompt("Begründung der Ablehnung (wird dokumentiert):");
      if (!accept && !note) return;
      await api("POST", `/api/terminations/${t.id}/process`, { accept, contract_id: assign[t.id] || t.contract_id, note });
      load(); onChange();
    } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); }
  }

  return (
    <Card className="mb-6">
      <h2 className="mb-1 font-medium">Eingegangene Kündigungen &amp; Widerrufe {open.length > 0 && <Badge tone="red">{open.length} offen</Badge>}</h2>
      <p className="mb-3 text-xs text-slate-500">Die Erklärung ist mit Eingang wirksam (Zugang). Bitte zeitnah zuordnen und bestätigen – der Kunde hat bereits eine Eingangsbestätigung erhalten.</p>
      {error && <div className="mb-2"><Alert>{error}</Alert></div>}
      <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
        {items.slice(0, 20).map((t) => (
          <li key={t.id} className="py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <strong>{t.kind === "termination" ? (t.termination_type === "extraordinary" ? "Außerordentliche Kündigung" : "Kündigung") : "Widerruf"}</strong> {t.number} von {t.name} ({t.email}) · eingegangen {new Date(t.received_at).toLocaleString("de-DE")} über {t.channel === "public" ? "Kündigungsseite" : t.channel === "portal" ? "Portal" : "manuell"}
              </span>
              <Badge tone={t.status === "received" ? "amber" : t.status === "processed" ? "green" : "slate"}>{t.status === "received" ? "offen" : t.status === "processed" ? `bestätigt${t.effective_date ? ` zum ${formatDate(t.effective_date)}` : ""}` : "abgelehnt"}</Badge>
            </div>
            <p className="text-xs text-slate-500">
              Kunden-Nr. {t.customer_number ?? "–"} · Vertrag {t.contract_number ?? t.contract_reference ?? "–"}{t.requested_date ? ` · gewünscht zum ${formatDate(t.requested_date)}` : ""}{t.reason ? ` · „${t.reason}“` : ""}
              {t.confirmation_sent_at ? " · Bestätigung versendet" : " · Bestätigung noch nicht versendet!"}
            </p>
            {t.status === "received" && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {!t.contract_id && (
                  <Select aria-label="Vertrag zuordnen" className="w-72" value={assign[t.id] ?? ""} onChange={(e) => setAssign({ ...assign, [t.id]: e.target.value })}>
                    <option value="">Vertrag zuordnen …</option>
                    {contracts.filter((c) => c.status === "active").map((c) => <option key={c.id} value={c.id}>{c.number} · {c.customer_name} · {c.title}</option>)}
                  </Select>
                )}
                <Button variant="primary" className="!h-7 !py-0 !text-xs" onClick={() => process(t, true)} disabled={!t.contract_id && !assign[t.id]}>{t.kind === "termination" ? "Kündigung ausführen" : "Widerruf ausführen"}</Button>
                <Button className="!h-7 !py-0 !text-xs" onClick={() => process(t, false)}>Ablehnen</Button>
                {t.kind === "withdrawal" && <span className="text-xs text-amber-700">Nach Widerruf: Rechnungen stornieren und Zahlungen binnen 14 Tagen erstatten (§ 357 BGB).</span>}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
