"use client";

import { useFeedback } from "@/components/Feedback";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { InvoiceEditor } from "@/components/InvoiceEditor";
import { InvoiceStatus } from "@/components/InvoiceStatus";
import { InvoiceView } from "@/components/InvoiceView";
import { ReceivablePanel } from "@/components/ReceivablePanel";
import { Alert, Button, Card, PageHeader } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";
import type { Invoice } from "@/lib/types";

interface Delivery {
  id: string;
  recipient: string;
  message_id: string;
  sent_at: string;
  sent_by: string;
}

function InvoiceDetail() {
  const fb = useFeedback();
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const params = useSearchParams();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };

  const load = useCallback(() => {
    api<Invoice>("GET", `/api/invoices/${id}`)
      .then((inv) => {
        setInvoice(inv);
        setEditing(false);
        if (inv.status === "issued") void api<Delivery[]>("GET", `/api/invoices/${id}/deliveries`).then(setDeliveries);
      })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, [id]);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const issue = () =>
    act(async () => {
      if (!await fb.confirm("Rechnung jetzt verbindlich ausstellen?\n\nSie erhält ihre fortlaufende Nummer und wird festgeschrieben. Danach ist keine Änderung mehr möglich – nur noch ein Storno.")) return;
      await api("POST", `/api/invoices/${id}/issue`);
      load();
    });

  const remove = () =>
    act(async () => {
      if (!await fb.confirm("Entwurf löschen?")) return;
      await api("DELETE", `/api/invoices/${id}`);
      router.push("/rechnungen");
    });

  const send = () =>
    act(async () => {
      if (!await fb.confirm("Rechnung jetzt per E-Mail an den Kunden senden (E-Rechnung + PDF)?")) return;
      const d = await api<Delivery>("POST", `/api/invoices/${id}/send`);
      setNotice(`Gesendet an ${d.recipient}.`);
      load();
    });

  const peppol = () =>
    act(async () => {
      if (!await fb.confirm("Rechnung über Peppol an den Kunden übermitteln?\n\nDie E-Rechnung geht über euren Access Point an die Peppol-ID des Kunden.")) return;
      const d = await api<Delivery>("POST", `/api/invoices/${id}/peppol`);
      setNotice(`Über Peppol übermittelt an ${d.recipient}.`);
      load();
    });

  const cancel = () =>
    act(async () => {
      const reason = await fb.prompt("Grund für das Storno (erscheint auf der Stornorechnung):");
      if (!reason) return;
      const storno = await api<Invoice>("POST", `/api/invoices/${id}/cancel`, { reason });
      router.push(`/rechnungen/${storno.id}`);
    });

  if (error && !invoice) return <Alert>{error}</Alert>;
  if (!invoice) return <p className="text-sm text-slate-500">Lade …</p>;

  const title = invoice.status === "draft" ? "Rechnungsentwurf" : `${invoice.kind === "cancellation" ? "Stornorechnung" : "Rechnung"} ${invoice.number}`;
  return (
    <>
      <PageHeader
        title={title}
        description={`${invoice.customer_name} · ${invoice.status === "draft" ? "noch nicht ausgestellt" : `ausgestellt am ${formatDate(invoice.issue_date)}`}`}
        actions={
          <>
            <Link href="/rechnungen" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">Zur Liste</Link>
            {invoice.status === "draft" && !editing && (
              <>
                <Button onClick={() => setEditing(true)} disabled={busy}>Bearbeiten</Button>
                <Button onClick={remove} disabled={busy}>Löschen</Button>
                <Button variant="primary" onClick={issue} disabled={busy}>{busy ? "Bitte warten …" : "Freigeben & festschreiben"}</Button>
              </>
            )}
            {invoice.status === "issued" && (
              <>
                <a href={`/api/invoices/${invoice.id}/pdf`} target="_blank" rel="noopener" className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800">PDF ansehen</a>
                <a href={`/api/invoices/${invoice.id}/pdf?download=true`} className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800">PDF</a>
                <a href={`/api/invoices/${invoice.id}/xml`} className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800">XML</a>
                {(invoice.snapshot as { buyer?: { einvoice_format?: string } } | null)?.buyer?.einvoice_format === "xrechnung" && (
                  <a href={`/api/invoices/${invoice.id}/xrechnung`} className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800">XRechnung</a>
                )}
                <Button onClick={send} disabled={busy}>Per E-Mail senden</Button>
                {(invoice.snapshot as { buyer?: { peppol_id?: string } } | null)?.buyer?.peppol_id && <Button onClick={peppol} disabled={busy}>Über Peppol senden</Button>}
              </>
            )}
            {invoice.status === "issued" && invoice.kind === "invoice" && !invoice.cancelled_by && (
              <Button variant="danger" onClick={cancel} disabled={busy}>Stornieren</Button>
            )}
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <InvoiceStatus invoice={invoice} />
        {invoice.cancelled_by && <Link className="text-sm text-brand-700 hover:underline dark:text-brand-100" href={`/rechnungen/${invoice.cancelled_by}`}>→ zur Stornorechnung</Link>}
        {invoice.cancels_invoice_id && <Link className="text-sm text-brand-700 hover:underline dark:text-brand-100" href={`/rechnungen/${invoice.cancels_invoice_id}`}>→ zur stornierten Rechnung</Link>}
      </div>
      {params.get("gespeichert") && invoice.status === "draft" && !editing && <div className="mb-4"><Alert kind="success">Entwurf gespeichert.</Alert></div>}
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {invoice.status === "draft" ? (
        editing ? (
          <InvoiceEditor invoice={invoice} />
        ) : (
          <Card>
            <p className="mb-3 text-sm text-slate-500">
              Leistung {formatDate(invoice.service_date_from)}{invoice.service_date_to ? ` – ${formatDate(invoice.service_date_to)}` : ""} · Zahlungsziel {invoice.due_days} Tage · Preise {invoice.price_mode === "net" ? "netto" : "brutto"}
            </p>
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {invoice.lines.map((l) => (
                <li key={l.position} className="flex justify-between py-2">
                  <span>{l.description}</span>
                  <span className="tabular text-slate-600">{l.quantity.replace(/\.?0+$/, "").replace(".", ",")} × {formatEuro(l.unit_price)}</span>
                </li>
              ))}
            </ul>
          </Card>
        )
      ) : (
        <>
          <InvoiceView invoice={invoice} />
          {invoice.kind === "invoice" && <ReceivablePanel invoiceId={invoice.id} />}
          <Card className="mt-4">
            <h2 className="mb-2 text-sm font-medium text-slate-500">Festschreibung (GoBD)</h2>
            <dl className="grid gap-1 text-xs md:grid-cols-[10rem_1fr]">
              <dt className="text-slate-500">Festgeschrieben</dt><dd>{invoice.issued_at ? new Date(invoice.issued_at).toLocaleString("de-DE") : "–"} durch {invoice.issued_by}</dd>
              <dt className="text-slate-500">Ledger-Datensatz</dt><dd className="font-mono">{invoice.ledger_record_id}</dd>
              <dt className="text-slate-500">Hash (SHA-256)</dt><dd className="break-all font-mono">{invoice.ledger_hash}</dd>
              <dt className="text-slate-500">Gesamtbetrag</dt><dd>{formatEuro(invoice.total_gross)}</dd>
              <dt className="text-slate-500">E-Rechnung</dt><dd>ZUGFeRD / Factur-X EN 16931 (PDF/A-3 mit eingebettetem XML), geprüft gegen XSD und EN-16931-Regeln</dd>
            </dl>
          </Card>
          <Card className="mt-4">
            <h2 className="mb-2 text-sm font-medium text-slate-500">Versand</h2>
            {deliveries.length === 0 ? (
              <p className="text-sm text-slate-500">Noch nicht versendet.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {deliveries.map((d) => (
                  <li key={d.id}>{new Date(d.sent_at).toLocaleString("de-DE")} an {d.recipient} <span className="text-xs text-slate-500">(durch {d.sent_by}, {d.message_id})</span></li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </>
  );
}

export default function InvoicePage() {
  return (
    <Suspense>
      <InvoiceDetail />
    </Suspense>
  );
}
