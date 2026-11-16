"use client";

import { dueLabel, formatRelative } from "@/lib/dates";
import { formatDate } from "@/lib/http";

/** Relatives Datum mit exaktem Datum als Tooltip; mode="due" für Fälligkeiten (rot bei Überfälligkeit). */
export function RelDate({ date, mode = "relative", className = "" }: { date: string; mode?: "relative" | "due"; className?: string }) {
  if (mode === "due") {
    const d = dueLabel(date);
    const tone = { red: "text-red-600 dark:text-red-400", amber: "text-amber-700 dark:text-amber-300", slate: "text-slate-500 dark:text-slate-400" }[d.tone];
    return <time dateTime={date} title={formatDate(date)} className={`whitespace-nowrap ${tone} ${className}`}>{d.text}</time>;
  }
  return <time dateTime={date} title={formatDate(date)} className={`whitespace-nowrap ${className}`}>{formatRelative(date)}</time>;
}
