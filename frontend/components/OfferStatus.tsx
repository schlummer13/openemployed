import { Badge } from "@/components/ui";
import type { InvoiceLine } from "@/lib/types";

export interface OfferSummary {
  id: string;
  number: string;
  customer_id: string;
  customer_name: string;
  customer_type: "b2b" | "b2c";
  customer_email: string;
  title: string;
  status: "draft" | "sent" | "accepted" | "declined" | "superseded";
  price_mode: "net" | "gross";
  valid_until: string;
  total_net: string;
  total_tax: string;
  total_gross: string;
  expired: boolean;
  revision_of: string | null;
  sent_at: string | null;
  decided_at: string | null;
  decided_by_name: string | null;
  decision_channel: string | null;
  decision_note: string | null;
  invoice_id: string | null;
  contract_id: string | null;
  intro_text: string | null;
  closing_text: string | null;
}

export interface Offer extends OfferSummary {
  lines: (InvoiceLine & { recurring: boolean })[];
}

export function OfferStatus({ offer }: { offer: OfferSummary }) {
  if (offer.status === "sent" && offer.expired) return <Badge tone="red">abgelaufen</Badge>;
  const map = {
    draft: ["Entwurf", "slate"],
    sent: ["versendet", "blue"],
    accepted: ["angenommen", "green"],
    declined: ["abgelehnt", "red"],
    superseded: ["ersetzt", "slate"],
  } as const;
  const [label, tone] = map[offer.status];
  return <Badge tone={tone}>{label}</Badge>;
}
