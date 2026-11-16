"use client";

import { InvoiceEditor } from "@/components/InvoiceEditor";
import { PageHeader } from "@/components/ui";

export default function NewInvoicePage() {
  return (
    <>
      <PageHeader title="Neue Rechnung" description="Wird als Entwurf gespeichert. Nummer und Festschreibung erst bei der Freigabe." />
      <InvoiceEditor />
    </>
  );
}
