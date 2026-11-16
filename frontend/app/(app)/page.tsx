import Link from "next/link";
import { Dashboard } from "@/components/Dashboard";
import { PageHeader, linkButtonClass, primaryLinkClass } from "@/components/ui";

export default function Home() {
  return (
    <div className="space-y-8">
      <PageHeader title="Übersicht" description="Was wirklich zur Verfügung steht, wie sich Einnahmen und Ausgaben entwickeln und was ansteht."
        actions={<><Link href="/angebote/neu" className={linkButtonClass}>Neues Angebot</Link><Link href="/rechnungen/neu" className={primaryLinkClass}>Neue Rechnung</Link></>} />
      <Dashboard />
    </div>
  );
}
