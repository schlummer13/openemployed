"use client";

import { KIND_STYLE, type Absence } from "@/components/Absences";

const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const iso = (y: number, m: number, d: number) => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

/** Jahresübersicht mit 12 Monaten: Feiertage (gesetzlich/betrieblich/halb), eigene Abwesenheiten, Wochenenden, heute. */
export function YearCalendar({ year, holidays, halfDays = {}, regional = {}, absences = [], workDays = [0, 1, 2, 3, 4], bridges = [] }: {
  year: number; holidays: Record<string, string>; halfDays?: Record<string, string>; regional?: Record<string, string>;
  absences?: Pick<Absence, "kind" | "kind_label" | "start_date" | "end_date" | "status">[]; workDays?: number[]; bridges?: string[];
}) {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Berlin" });
  const bridgeSet = new Set(bridges);
  return (
    <div>
      <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {MONTHS.map((name, m) => {
          const days = new Date(Date.UTC(year, m + 1, 0)).getUTCDate();
          const lead = (new Date(Date.UTC(year, m, 1)).getUTCDay() + 6) % 7;
          return (
            <div key={name}>
              <p className="mb-1.5 text-sm font-semibold text-slate-800 dark:text-slate-200">{name}</p>
              <div className="grid grid-cols-7 gap-0.5 text-center text-[11px]">
                {WD.map((w) => <span key={w} className="pb-0.5 font-medium text-slate-400">{w}</span>)}
                {Array.from({ length: lead }, (_, i) => <span key={`l${i}`} />)}
                {Array.from({ length: days }, (_, i) => {
                  const d = iso(year, m, i + 1);
                  const wd = (lead + i) % 7;
                  const hol = holidays[d];
                  const half = halfDays[d];
                  const reg = regional[d];
                  const abs = absences.find((a) => a.start_date <= d && a.end_date >= d && (a.status === "approved" || a.status === "requested"));
                  const off = !workDays.includes(wd);
                  const title = [hol, half && `${half} (halber Tag)`, reg && `regional: ${reg}`, abs && `${abs.kind_label}${abs.status === "requested" ? " (beantragt)" : ""}`,
                    bridgeSet.has(d) && "Brückentag"].filter(Boolean).join(" · ");
                  let cls = off ? "text-slate-400 dark:text-slate-600" : "text-slate-700 dark:text-slate-300";
                  if (abs && !hol && !off) cls = abs.status === "requested" ? `ring-2 ring-inset ring-amber-400 text-slate-800 dark:text-slate-100` : `${KIND_STYLE[abs.kind] ?? "bg-slate-500 text-white"}`;
                  else if (hol) cls = "bg-amber-400 font-semibold text-amber-950";
                  else if (half) cls = "bg-[linear-gradient(135deg,var(--color-amber-300)_50%,transparent_50%)] font-medium text-slate-800 dark:text-slate-100";
                  else if (reg) cls += " underline decoration-amber-500 decoration-2 underline-offset-2";
                  else if (bridgeSet.has(d)) cls += " bg-sky-100 dark:bg-sky-950/60";
                  return (
                    <span key={d} title={title || undefined}
                      className={`grid aspect-square place-items-center rounded-md tabular-nums ${cls} ${d === today ? "outline outline-2 outline-offset-1 outline-brand-500" : ""}`}>
                      {i + 1}
                    </span>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-5 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-500">
        <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-amber-400" />Feiertag / betrieblich frei</span>
        <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-[linear-gradient(135deg,var(--color-amber-300)_50%,transparent_50%)] ring-1 ring-inset ring-slate-200 dark:ring-slate-700" />halber Tag frei</span>
        {Object.keys(regional).length > 0 && <span className="flex items-center gap-1.5"><span className="underline decoration-amber-500 decoration-2">12</span>regional</span>}
        {bridges.length > 0 && <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-sky-100 dark:bg-sky-950" />Brückentag</span>}
        {absences.length > 0 && <>
          <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-teal-500" />Urlaub</span>
          <span className="flex items-center gap-1.5"><span className="size-3 rounded ring-2 ring-inset ring-amber-400" />beantragt</span>
        </>}
        <span className="flex items-center gap-1.5"><span className="size-3 rounded outline outline-2 outline-brand-500" />heute</span>
      </div>
    </div>
  );
}
