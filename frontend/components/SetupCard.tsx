"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "./Feedback";
import { Icon } from "./Icon";
import { Card } from "./ui";
import { api, HttpError } from "@/lib/http";

interface Item { key: string; label: string; done: boolean; hint: string; href: string | null; manual?: boolean; done_by?: string | null }
interface Setup { items: Item[]; done: number; total: number }

/** Checkliste für den Produktivstart – verschwindet, sobald alles erledigt ist (oder eingeklappt wurde). */
export function SetupCard() {
  const [data, setData] = useState<Setup | null>(null);
  const [open, setOpen] = useState(true);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<Setup>("GET", "/api/setup").then(setData).catch(() => undefined); }, []);
  useEffect(load, [load]);
  useEffect(() => { try { if (localStorage.getItem("setup.collapsed") === "1") setOpen(false); } catch { /* ohne Speicher */ } }, []);
  if (!data || data.done === data.total) return null;

  const toggle = () => setOpen((o) => { try { localStorage.setItem("setup.collapsed", o ? "1" : "0"); } catch { /* ohne Speicher */ } return !o; });
  const mark = async (item: Item) => {
    try {
      await api("PUT", `/api/setup/${item.key}`, { done: !item.done });
      toast(item.done ? `„${item.label}“ wieder offen.` : `„${item.label}“ erledigt.`);
      load();
    } catch (e) { toast(e instanceof HttpError ? e.message : "Speichern fehlgeschlagen", "error"); }
  };
  const pct = Math.round((data.done / data.total) * 100);
  const open_ = data.items.filter((i) => !i.done);
  return (
    <Card className="!p-0">
      <button type="button" onClick={toggle} aria-expanded={open} className="flex w-full items-center gap-4 px-5 py-4 text-left sm:px-6">
        <span className="relative grid size-11 shrink-0 place-items-center">
          <svg viewBox="0 0 36 36" className="absolute inset-0 -rotate-90" aria-hidden="true">
            <circle cx="18" cy="18" r="15.5" fill="none" strokeWidth="3" className="stroke-slate-100 dark:stroke-white/10" />
            <circle cx="18" cy="18" r="15.5" fill="none" strokeWidth="3" strokeLinecap="round" className="stroke-brand-500"
              strokeDasharray={`${(pct / 100) * 97.4} 97.4`} />
          </svg>
          <span className="text-[11px] font-semibold tabular">{pct}%</span>
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">Einrichtung für den Produktivstart</span>
          <span className="block text-[13px] text-slate-500">{data.done} von {data.total} erledigt · als Nächstes: {open_[0]?.label}</span>
        </span>
        <Icon name="chevron" className={`size-4 shrink-0 text-slate-400 transition ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <ul className="grid gap-px border-t border-slate-100 bg-slate-100 sm:grid-cols-2 dark:border-white/5 dark:bg-white/5">
          {data.items.map((i) => (
            <li key={i.key} className="flex items-start gap-3 bg-white px-5 py-3 sm:px-6 sm:[&:last-child:nth-child(odd)]:col-span-2 dark:bg-slate-900">
              {i.manual ? (
                <input type="checkbox" className="mt-0.5" checked={i.done} onChange={() => void mark(i)} aria-label={`${i.label} als erledigt markieren`} />
              ) : (
                <span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full ${i.done ? "bg-emerald-500 text-white" : "ring-1 ring-inset ring-slate-300 dark:ring-white/20"}`}>
                  {i.done && <Icon name="check" className="size-2.5" />}
                </span>
              )}
              <span className="min-w-0 flex-1 text-sm">
                {i.href && !i.done ? <Link href={i.href} className="font-medium hover:underline">{i.label}</Link>
                  : <span className={`font-medium ${i.done ? "text-slate-400 line-through decoration-slate-300" : ""}`}>{i.label}</span>}
                <span className="block text-xs text-slate-500">{i.done && i.done_by ? `bestätigt von ${i.done_by}` : i.hint}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
