"use client";

import { InvoiceEditor } from "@/components/InvoiceEditor";
import { PageHeader } from "@/components/ui";

export default function NewOfferPage() {
  return (
    <>
      <PageHeader title="Neues Angebot" description="Wird als Entwurf gespeichert. Positionen mit „Monatl.“ werden bei Annahme zum Vertrag, die übrigen zur Rechnung." />
      <InvoiceEditor kind="offer" />
    </>
  );
}
