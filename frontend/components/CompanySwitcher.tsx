"use client";

import { useEffect, useRef, useState } from "react";
import { useUser } from "./AuthGate";
import { useFeedback } from "./Feedback";
import { Icon } from "./Icon";
import { api, HttpError } from "@/lib/http";

interface Company { id: string; name: string; legal_name: string | null; role: string; role_label: string }

/** Firmenauswahl (Mandanten) – erscheint, sobald es mehr als eine Firma gibt oder für die Geschäftsführung. */
export function CompanySwitcher() {
  const me = useUser();
  const [data, setData] = useState<{ current: string; companies: Company[] } | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { toast, prompt } = useFeedback();
  useEffect(() => { void api<{ current: string; companies: Company[] }>("GET", "/api/companies").then(setData).catch(() => undefined); }, []);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  if (!data || (data.companies.length < 2 && me?.permissions?.admin !== "write")) return null;
  const current = data.companies.find((c) => c.id === data.current) ?? data.companies[0];
  const select = async (id: string) => {
    try { await api("POST", "/api/companies/select", { company_id: id }); window.location.assign("/"); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Wechsel fehlgeschlagen", "error"); }
  };
  const create = async () => {
    const name = await prompt({ title: "Neue Firma anlegen", confirmLabel: "Anlegen", message: "Eigene Buchhaltung, Rechnungsnummern, Kunden und Belege – Benutzer und Systemeinstellungen werden geteilt.", input: { label: "Name der Firma", minLength: 2 } });
    if (!name) return;
    try { const r = await api<{ id: string }>("POST", "/api/companies", { name }); await api("POST", "/api/companies/select", { company_id: r.id }); window.location.assign("/einrichtung"); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Anlegen fehlgeschlagen", "error"); }
  };
  return (
    <div ref={ref} className="relative px-1">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-sm hover:bg-slate-200/50 dark:hover:bg-white/[0.04]">
        <Icon name="building" className="size-4 shrink-0 text-slate-400" />
        <span className="min-w-0 flex-1 truncate font-medium">{current?.legal_name ?? current?.name}</span>
        <Icon name="chevron" className={`size-3.5 shrink-0 text-slate-400 transition ${open ? "-rotate-90" : "rotate-90"}`} />
      </button>
      {open && (
        <div className="absolute inset-x-1 top-10 z-50 rounded-xl bg-white p-1 shadow-pop dark:bg-slate-900">
          {data.companies.map((c) => (
            <button key={c.id} type="button" onClick={() => void select(c.id)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-white/5">
              <span className="min-w-0 flex-1"><span className="block truncate font-medium">{c.legal_name ?? c.name}</span><span className="block text-xs text-slate-500">{c.role_label}</span></span>
              {c.id === data.current && <Icon name="check" className="size-4 text-brand-600" />}
            </button>
          ))}
          {me?.permissions?.admin === "write" && <button type="button" onClick={() => void create()} className="mt-1 w-full rounded-lg border-t border-slate-100 px-2.5 py-2 text-left text-sm text-brand-600 hover:bg-slate-100 dark:border-white/5 dark:text-brand-300 dark:hover:bg-white/5">+ Neue Firma</button>}
        </div>
      )}
    </div>
  );
}
