"use client";

import Link from "next/link";
import { MyAbsences } from "@/components/Absences";
import { useUser } from "@/components/AuthGate";
import { linkButtonClass, PageHeader } from "@/components/ui";
import { api } from "@/lib/http";

export default function AbwesenheitPage() {
  const me = useUser();
  const canApprove = me?.permissions?.payroll === "write";
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Mein Bereich" title="Urlaub & Abwesenheit" description="Urlaubskonto, Anträge und Krankmeldungen."
        actions={canApprove ? <Link href="/abwesenheit/team" className={linkButtonClass}>Team-Kalender</Link> : undefined} />
      <MyAbsences base="/api/me" fetcher={api} />
    </div>
  );
}
