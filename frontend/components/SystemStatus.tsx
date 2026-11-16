"use client";

import { useEffect, useState } from "react";
import { getHealth, type HealthResponse } from "@/lib/api";

const LABELS: Record<string, string> = {
  database: "Datenbank",
  ledger: "Festschreibung (Rust)",
};

export function SystemStatus() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    const load = () =>
      getHealth()
        .then((h) => active && (setHealth(h), setFailed(false)))
        .catch(() => active && setFailed(true));
    void load();
    const id = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const items: [string, boolean | null][] = failed
    ? [["Backend", false]]
    : [["Backend", health ? true : null], ...Object.entries(health?.checks ?? {}).map(([k, v]) => [LABELS[k] ?? k, v] as [string, boolean])];

  return (
    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-3 text-sm font-medium text-slate-500">Systemstatus</h2>
      <ul className="flex flex-wrap gap-3 text-sm">
        {items.map(([label, ok]) => (
          <li key={label} className="flex items-center gap-2 rounded-full border border-slate-200 px-3 py-1 dark:border-slate-700">
            <span
              aria-hidden
              className={`h-2 w-2 rounded-full ${ok === null ? "bg-slate-300" : ok ? "bg-emerald-500" : "bg-red-500"}`}
            />
            {label}
            <span className="sr-only">{ok === null ? "wird geprüft" : ok ? "bereit" : "nicht erreichbar"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
