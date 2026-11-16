"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";
import { NAV_GROUPS } from "./Nav";

const ACTIONS = [
  { label: "Neue Rechnung", href: "/rechnungen/neu", group: "Aktionen" },
  { label: "Neues Angebot", href: "/angebote/neu", group: "Aktionen" },
  { label: "Beleg fotografieren", href: "/erfassen", group: "Aktionen" },
  { label: "Zeit erfassen", href: "/zeiten", group: "Aktionen" },
  { label: "Reise abrechnen", href: "/reisekosten", group: "Aktionen" },
  { label: "Lohnabrechnung erstellen", href: "/mitarbeiter?tab=lohn", group: "Aktionen" },
  { label: "Mitarbeiter anlegen", href: "/mitarbeiter?neu=1", group: "Aktionen" },
  { label: "Kontoauszug importieren", href: "/buchhaltung", group: "Aktionen" },
];

/** Schnellnavigation (⌘K / Strg+K): Seiten und häufige Aktionen per Tastatur. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const items = useMemo(() => {
    const all = [...NAV_GROUPS.flatMap((g) => g.items.map((i) => ({ label: i.label, href: i.href, group: g.title ?? "Start" }))), ...ACTIONS];
    const needle = q.trim().toLowerCase();
    return needle ? all.filter((i) => i.label.toLowerCase().includes(needle) || i.group.toLowerCase().includes(needle)) : all;
  }, [q]);

  useEffect(() => {
    if (open) { setQ(""); setIdx(0); input.current?.focus(); }
  }, [open]);
  useEffect(() => { setIdx(0); }, [q]);
  if (!open) return null;

  const go = (href: string) => { onClose(); router.push(href); };
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label="Schnellnavigation">
      <div className="absolute inset-0 bg-slate-950/30 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-lg animate-pop overflow-hidden rounded-2xl bg-white shadow-pop dark:bg-slate-900">
        <div className="flex items-center gap-2.5 border-b border-slate-100 px-4 dark:border-slate-800">
          <Icon name="search" className="size-4 shrink-0 text-slate-400" />
          <input ref={input} autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Wohin möchtest du?" aria-label="Suchen"
            className="h-12 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-slate-400"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, items.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
              else if (e.key === "Enter" && items[idx]) go(items[idx].href);
              else if (e.key === "Escape") onClose();
            }} />
          <kbd className="rounded-md border border-slate-200 px-1.5 py-0.5 text-[11px] text-slate-400 dark:border-slate-700">Esc</kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto p-1.5" role="listbox">
          {items.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate-500">Nichts gefunden.</li>}
          {items.map((it, i) => (
            <li key={`${it.group}-${it.href}-${it.label}`} role="option" aria-selected={i === idx}>
              <button type="button" onMouseEnter={() => setIdx(i)} onClick={() => go(it.href)}
                className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm ${i === idx ? "bg-brand-50 text-brand-800 dark:bg-brand-500/15 dark:text-brand-100" : "text-slate-700 dark:text-slate-300"}`}>
                <span className="truncate">{it.label}</span>
                <span className="shrink-0 text-xs text-slate-400">{it.group}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
