"use client";

import { useState } from "react";
import { MyDocuments } from "@/components/Personnel";
import { TimeAccount } from "@/components/TimeAccount";
import { TimeClock } from "@/components/TimeClock";
import { PageHeader, Tabs } from "@/components/ui";
import { api } from "@/lib/http";

export default function StempeluhrPage() {
  const [tab, setTab] = useState<"clock" | "account" | "docs">("clock");
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Mein Bereich" title="Stempeluhr" description="Ein Klick zum Ein- und Ausstempeln – die gesetzliche Pause wird automatisch abgezogen." />
      <Tabs value={tab} onChange={setTab} items={[{ value: "clock", label: "Stempeluhr" }, { value: "account", label: "Mein Zeitkonto" }, { value: "docs", label: "Meine Dokumente" }]} />
      {tab === "clock" && <TimeClock base="/api/me" fetcher={api} />}
      {tab === "account" && <TimeAccount path="/api/me/time-account" fetcher={api} />}
      {tab === "docs" && <MyDocuments base="/api/me" fetcher={api} />}
    </div>
  );
}
