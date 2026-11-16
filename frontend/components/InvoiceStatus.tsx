import { Badge } from "@/components/ui";
import type { InvoiceSummary } from "@/lib/types";

export function InvoiceStatus({ invoice: i }: { invoice: InvoiceSummary }) {
  if (i.status === "draft") return <Badge>Entwurf</Badge>;
  if (i.kind === "cancellation") return <Badge tone="red">Stornorechnung</Badge>;
  if (i.cancelled_by) return <Badge tone="amber">Storniert</Badge>;
  return <Badge tone="green">Ausgestellt</Badge>;
}
