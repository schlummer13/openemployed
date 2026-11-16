"use client";

import { useEffect, useState, type ReactNode } from "react";
import { publicApi } from "@/lib/http";

interface Company { name: string; email: string | null; street: string | null; city: string | null; logo: string | null }

export function PublicShell({ children, title }: { children: ReactNode; title: string }) {
  const [company, setCompany] = useState<Company | null>(null);
  useEffect(() => { void publicApi<Company>("GET", "/api/public/company").then(setCompany).catch(() => undefined); }, []);
  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-white/80 backdrop-blur-xl dark:border-white/5 dark:bg-slate-950/80">
        <div className="mx-auto flex h-16 max-w-4xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            {company?.logo ? <img src={company.logo} alt={company.name} className="h-9 max-w-36 shrink-0 rounded-md object-contain dark:bg-white dark:p-1" /> : null}
            <span className={`truncate font-semibold tracking-tight ${company?.logo ? "hidden sm:inline" : ""}`}>{company?.name}</span>
          </div>
          <span className="shrink-0 whitespace-nowrap rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 dark:bg-white/5 dark:text-slate-300">{title}</span>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-10 sm:px-6">{children}</main>
      <footer className="mx-auto max-w-4xl px-4 pb-10 text-xs leading-relaxed text-slate-500 sm:px-6">
        {company?.name}{company?.street ? ` · ${company.street}` : ""}{company?.city ? ` · ${company.city}` : ""}{company?.email ? ` · ${company.email}` : ""}
        {" · "}<a className="underline" href="/kuendigen">Verträge hier kündigen</a>
      </footer>
    </div>
  );
}
