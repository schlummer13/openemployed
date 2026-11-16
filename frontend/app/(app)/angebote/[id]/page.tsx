"use client";

import { useFeedback } from "@/components/Feedback";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { InvoiceEditor } from "@/components/InvoiceEditor";
import { OfferStatus, type Offer } from "@/components/OfferStatus";
import { SignaturePanel } from "@/components/SignaturePanel";
import { Alert, Button, Card, Field, Input, PageHeader, Textarea } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";
import { UNITS } from "@/lib/types";

const linkClass = "inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800";

function plusDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function firstOfNextMonth(): string {
  const d = new Date();
  const f = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  return `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}-01`;
}

function OfferDetail() {
  const fb = useFeedback();
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const params = useSearchParams();
  const [offer, setOffer] = useState<Offer | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [decision, setDecision] = useState<{ accept: boolean; name: string; note: string } | null>(null);
  const [contractStart, setContractStart] = useState(firstOfNextMonth());

  const load = useCallback(() => {
    api<Offer>("GET", `/api/offers/${id}`)
      .then((o) => { setOffer(o); setEditing(false); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, [id]);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }

  if (!offer) return error ? <Alert>{error}</Alert> : <p className="text-sm text-slate-500">Lade …</p>;
  if (editing) {
    return (
      <>
        <PageHeader title={`Angebot ${offer.number} bearbeiten`} />
        <InvoiceEditor kind="offer" offer={offer} />
      </>
    );
  }

  const gross = offer.price_mode === "gross";
  const hasRecurring = offer.lines.some((l) => l.recurring);
  const send = (email: boolean) => act(async () => {
    const q = email ? `Angebot jetzt per E-Mail an ${offer.customer_email} senden?` : "Angebot als versendet markieren (ohne E-Mail)?";
    if (!await fb.confirm(`${q}\n\nDanach ist der Inhalt unveränderlich – Änderungen nur noch über eine neue Version.`)) return;
    await api("POST", `/api/offers/${id}/send`, { email });
    load();
  });
  const remove = () => act(async () => {
    if (!await fb.confirm("Entwurf löschen?")) return;
    await api("DELETE", `/api/offers/${id}`);
    router.push("/angebote");
  });
  const revise = () => act(async () => {
    const o = await api<Offer>("POST", `/api/offers/${id}/revision`, { valid_until: plusDays(30) });
    router.push(`/angebote/${o.id}`);
  });
  const decide = () => decision && act(async () => {
    await api("POST", `/api/offers/${id}/decision`, { accept: decision.accept, name: decision.name, note: decision.note || null });
    setDecision(null);
    load();
  });
  const convert = () => act(async () => {
    await api("POST", `/api/offers/${id}/convert`, { contract_start: hasRecurring ? contractStart : null });
    load();
  });

  return (
    <>
      <PageHeader
        title={`Angebot ${offer.number}`}
        description={`${offer.title} · ${offer.customer_name}`}
        actions={
          <div className="flex flex-wrap gap-2">
            <a href={`/api/offers/${offer.id}/pdf`} target="_blank" rel="noopener" className={linkClass}>PDF ansehen</a>
            {offer.status === "draft" && (
              <>
                <Button onClick={() => setEditing(true)} disabled={busy}>Bearbeiten</Button>
                <Button variant="danger" onClick={remove} disabled={busy}>Löschen</Button>
                <Button onClick={() => send(false)} disabled={busy}>Nur als versendet markieren</Button>
                <Button variant="primary" onClick={() => send(true)} disabled={busy}>Per E-Mail senden</Button>
              </>
            )}
            {offer.status === "sent" && (
              <>
                <Button onClick={revise} disabled={busy}>Neue Version</Button>
                <Button variant="danger" onClick={() => setDecision({ accept: false, name: "", note: "" })} disabled={busy}>Abgelehnt</Button>
                <Button variant="primary" onClick={() => setDecision({ accept: true, name: "", note: "" })} disabled={busy || offer.expired}>Angenommen</Button>
              </>
            )}
            {offer.status === "declined" && <Button onClick={revise} disabled={busy}>Neue Version</Button>}
          </div>
        }
      />
      {params.get("gespeichert") && offer.status === "draft" && <div className="mb-4"><Alert kind="success">Entwurf gespeichert.</Alert></div>}
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {offer.expired && <div className="mb-4"><Alert kind="warning">Das Angebot ist seit {formatDate(offer.valid_until)} abgelaufen. Für eine Annahme bitte eine neue Version mit neuem Gültigkeitsdatum anlegen.</Alert></div>}

      {decision && (
        <Card className="mb-4">
          <h2 className="mb-3 font-medium">{decision.accept ? "Annahme erfassen" : "Ablehnung erfassen"}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Name der Person beim Kunden"><Input required value={decision.name} onChange={(e) => setDecision({ ...decision, name: e.target.value })} /></Field>
            <Field label="Notiz (z. B. „per E-Mail vom …“)"><Textarea rows={2} value={decision.note} onChange={(e) => setDecision({ ...decision, note: e.target.value })} /></Field>
          </div>
          <div className="mt-3 flex gap-2">
            <Button variant="primary" onClick={decide} disabled={busy || decision.name.trim().length < 2}>Speichern</Button>
            <Button onClick={() => setDecision(null)}>Abbrechen</Button>
          </div>
        </Card>
      )}

      {offer.status === "accepted" && (
        <Card className="mb-4">
          <p className="text-sm">
            Angenommen am {formatDate(offer.decided_at?.slice(0, 10))} von <strong>{offer.decided_by_name}</strong>
            {offer.decision_channel === "portal" ? " (Kundenportal)" : offer.decision_channel === "signature" ? " (Signatur)" : " (manuell erfasst)"}
            {offer.decision_note ? ` – ${offer.decision_note}` : ""}.
          </p>
          {offer.invoice_id || offer.contract_id ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {offer.invoice_id && <Link href={`/rechnungen/${offer.invoice_id}`} className={linkClass}>Rechnungsentwurf öffnen</Link>}
              {offer.contract_id && <Link href={`/vertraege?id=${offer.contract_id}`} className={linkClass}>Vertragsentwurf öffnen</Link>}
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap items-end gap-3">
              {hasRecurring && (
                <Field label="Vertragsbeginn"><Input type="date" value={contractStart} onChange={(e) => setContractStart(e.target.value)} /></Field>
              )}
              <Button variant="primary" onClick={convert} disabled={busy}>
                {hasRecurring && offer.lines.some((l) => !l.recurring) ? "Rechnung + Vertrag anlegen" : hasRecurring ? "Vertrag anlegen" : "Rechnung anlegen"}
              </Button>
            </div>
          )}
        </Card>
      )}
      {offer.status === "declined" && (
        <div className="mb-4"><Alert kind="info">Abgelehnt am {formatDate(offer.decided_at?.slice(0, 10))} von {offer.decided_by_name}{offer.decision_note ? ` – ${offer.decision_note}` : ""}.</Alert></div>
      )}

      <Card>
        <dl className="mb-4 grid gap-2 text-sm md:grid-cols-4">
          <div><dt className="text-slate-500">Status</dt><dd><OfferStatus offer={offer} /></dd></div>
          <div><dt className="text-slate-500">Gültig bis</dt><dd className="tabular">{formatDate(offer.valid_until)}</dd></div>
          <div><dt className="text-slate-500">Versendet</dt><dd className="tabular">{formatDate(offer.sent_at?.slice(0, 10))}</dd></div>
          <div><dt className="text-slate-500">Version von</dt><dd>{offer.revision_of ? <Link className="text-brand-700 hover:underline" href={`/angebote/${offer.revision_of}`}>vorheriges Angebot</Link> : "–"}</dd></div>
        </dl>
        {offer.intro_text && <p className="mb-3 whitespace-pre-line text-sm">{offer.intro_text}</p>}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="pb-2 font-medium">Pos.</th><th className="pb-2 font-medium">Bezeichnung</th>
                <th className="pb-2 text-right font-medium">Menge</th><th className="pb-2 text-right font-medium">Preis {gross ? "brutto" : "netto"}</th>
                <th className="pb-2 pl-6 font-medium">Art</th>
              </tr>
            </thead>
            <tbody>
              {offer.lines.map((l) => (
                <tr key={l.position} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-2">{l.position}</td>
                  <td className="py-2">{l.description}</td>
                  <td className="py-2 text-right tabular">{l.quantity.replace(/\.?0+$/, "").replace(".", ",")} {UNITS.find(([v]) => v === l.unit_code)?.[1] ?? l.unit_code}</td>
                  <td className="py-2 text-right tabular">{formatEuro(l.unit_price)}</td>
                  <td className="py-2 pl-6">{l.recurring ? "monatlich" : "einmalig"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <dl className="ml-auto mt-4 max-w-xs space-y-1 text-sm tabular">
          <div className="flex justify-between"><dt className="text-slate-500">Summe netto</dt><dd>{formatEuro(offer.total_net)}</dd></div>
          <div className="flex justify-between"><dt className="text-slate-500">USt</dt><dd>{formatEuro(offer.total_tax)}</dd></div>
          <div className="flex justify-between border-t border-slate-200 pt-1 font-semibold dark:border-slate-700"><dt>Gesamt</dt><dd>{formatEuro(offer.total_gross)}</dd></div>
          {hasRecurring && <p className="pt-1 text-xs text-slate-500">Monatliche Positionen sind mit ihrem Monatsbetrag enthalten.</p>}
        </dl>
        {offer.closing_text && <p className="mt-3 whitespace-pre-line text-sm">{offer.closing_text}</p>}
      </Card>
      {offer.status !== "draft" && offer.status !== "superseded" && (
        <SignaturePanel subjectType="offer" subjectId={offer.id} canRequest={offer.status === "sent" && !offer.expired}
          defaultName={offer.customer_type === "b2c" ? offer.customer_name : ""} defaultEmail={offer.customer_email} onChange={load} />
      )}
    </>
  );
}

export default function OfferDetailPage() {
  return <Suspense><OfferDetail /></Suspense>;
}
