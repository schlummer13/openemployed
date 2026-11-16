"use client";

import { RelDate } from "@/components/RelDate";
import Link from "next/link";
import { useEffect, useState } from "react";
import { OfferStatus, type OfferSummary } from "@/components/OfferStatus";
import { Alert, Card, Empty, PageHeader, Table } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";

export default function OffersPage() {
  const [items, setItems] = useState<OfferSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<OfferSummary[]>("GET", "/api/offers")
      .then(setItems)
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, []);

  return (
    <>
      <PageHeader
        title="Angebote"
        description="Nach dem Versand ist ein Angebot unveränderlich – Änderungen laufen über eine neue Version. Angenommene Angebote werden mit einem Klick zu Rechnungsentwurf (einmalige Positionen) und Vertragsentwurf (monatliche Positionen)."
        actions={<Link href="/angebote/neu" className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm shadow-brand-600/20 transition hover:bg-brand-700">+ Angebot</Link>}
      />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <Card>
        {items && items.length === 0 ? (
          <Empty>Noch keine Angebote.</Empty>
        ) : (
          <Table
            head={
              <tr>
                <th className="pb-2 font-medium">Nummer</th>
                <th className="pb-2 font-medium">Kunde</th>
                <th className="pb-2 font-medium">Betreff</th>
                <th className="pb-2 font-medium">Gültig bis</th>
                <th className="pb-2 text-right font-medium">Betrag</th>
                <th className="pb-2 pl-6 font-medium">Status</th>
              </tr>
            }
          >
            {(items ?? []).map((o) => (
              <tr key={o.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <td className="py-2 tabular">
                  <Link href={`/angebote/${o.id}`} className="font-medium text-brand-700 hover:underline dark:text-brand-100">{o.number}</Link>
                </td>
                <td className="py-2">{o.customer_name}</td>
                <td className="py-2">{o.title}</td>
                <td className="py-2 tabular">{formatDate(o.valid_until)}{o.status === "sent" && <span className="block text-xs"><RelDate date={o.valid_until} mode="due" /></span>}</td>
                <td className="py-2 text-right tabular">{formatEuro(o.price_mode === "gross" ? o.total_gross : o.total_net)}{o.price_mode === "net" && <span className="text-xs text-slate-500"> netto</span>}</td>
                <td className="py-2 pl-6"><OfferStatus offer={o} /></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
