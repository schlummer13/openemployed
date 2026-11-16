"use client";

import { RelDate } from "@/components/RelDate";
import Link from "next/link";
import { useEffect, useState } from "react";
import { InvoiceStatus } from "@/components/InvoiceStatus";
import { Alert, Card, Empty, PageHeader, Table } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, formatDate, HttpError } from "@/lib/http";
import type { InvoiceSummary } from "@/lib/types";

export default function InvoicesPage() {
  const [items, setItems] = useState<InvoiceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<InvoiceSummary[]>("GET", "/api/invoices")
      .then(setItems)
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, []);

  return (
    <>
      <PageHeader
        title="Rechnungen"
        description="Entwürfe sind frei änderbar. Mit der Freigabe wird die Rechnung festgeschrieben und erhält ihre fortlaufende Nummer – danach ist nur noch ein Storno möglich."
        actions={<Link href="/rechnungen/neu" className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm shadow-brand-600/20 transition hover:bg-brand-700">+ Rechnung</Link>}
      />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <Card>
        {items && items.length === 0 ? (
          <Empty>Noch keine Rechnungen.</Empty>
        ) : (
          <Table
            head={
              <tr>
                <th className="pb-2 font-medium">Nummer</th>
                <th className="pb-2 font-medium">Kunde</th>
                <th className="pb-2 font-medium">Datum</th>
                <th className="pb-2 font-medium">Fällig</th>
                <th className="pb-2 text-right font-medium">Betrag</th>
                <th className="pb-2 font-medium">Status</th>
              </tr>
            }
          >
            {(items ?? []).map((i) => (
              <tr key={i.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <td className="py-2 tabular">
                  <Link href={`/rechnungen/${i.id}`} className="font-medium text-brand-700 hover:underline dark:text-brand-100">
                    {i.number ?? "Entwurf"}
                  </Link>
                </td>
                <td className="py-2">{i.customer_name}</td>
                <td className="py-2 tabular">{formatDate(i.issue_date)}</td>
                <td className="py-2 tabular">{i.due_date ? <>{formatDate(i.due_date)}<span className="block text-xs text-slate-500"><RelDate date={i.due_date} /></span></> : "–"}</td>
                <td className="py-2 text-right tabular">{formatEuro(i.total_gross)}</td>
                <td className="py-2"><InvoiceStatus invoice={i} /></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
