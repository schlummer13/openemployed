"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CompanySwitcher } from "./CompanySwitcher";
import { Logo, Wordmark } from "./Logo";
import { useEffect, useState, type ReactNode } from "react";
import { CommandPalette } from "./CommandPalette";
import { FeedbackProvider } from "./Feedback";
import { Icon } from "./Icon";
import { Nav, NAV_GROUPS } from "./Nav";
import { UserMenu } from "./UserMenu";

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <Logo size={32} className="shrink-0 drop-shadow-sm" />
      <div className="min-w-0 leading-tight">
        <p className="text-[15px]"><Wordmark /></p>
        <p className="text-[11px] text-slate-500 dark:text-slate-400">Open Source · Self-Employed</p>
      </div>
    </div>
  );
}

function SearchButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="flex h-9 w-full items-center gap-2 rounded-lg bg-white px-2.5 text-sm text-slate-400 shadow-card transition hover:text-slate-600 dark:bg-slate-900 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.07)] dark:hover:text-slate-200">
      <Icon name="search" className="size-4" />
      <span className="flex-1 text-left">Suchen …</span>
      <kbd className="rounded border border-slate-200 px-1 text-[10px] font-medium dark:border-slate-700">⌘K</kbd>
    </button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const pathname = usePathname();
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const group = NAV_GROUPS.find((g) => g.items.some((i) => isActive(i.href)));
  const current = group?.items.find((i) => isActive(i.href));

  return (
    <FeedbackProvider>
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col gap-5 border-r border-slate-200/70 bg-slate-50 px-3 py-5 md:flex dark:border-white/5 dark:bg-slate-950">
        <Brand />
        <CompanySwitcher />
        <div className="px-1"><SearchButton onClick={() => setPalette(true)} /></div>
        <Nav />
        <UserMenu />
      </aside>

      {open && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-sm" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-[min(18rem,85vw)] animate-in flex-col gap-5 bg-slate-50 px-3 py-5 shadow-pop dark:bg-slate-950">
            <div className="flex items-center justify-between"><Brand />
              <button aria-label="Navigation schließen" className="rounded-lg p-1.5 hover:bg-slate-200 dark:hover:bg-slate-800" onClick={() => setOpen(false)}><Icon name="x" className="size-5" /></button>
            </div>
            <CompanySwitcher />
            <Nav onNavigate={() => setOpen(false)} />
            <UserMenu />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col bg-white/40 dark:bg-transparent">
        <header data-topbar className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b border-slate-200/70 bg-white/75 px-4 backdrop-blur-xl backdrop-saturate-150 md:px-10 dark:border-white/5 dark:bg-slate-950/75">
          <button aria-label="Navigation öffnen" className="-ml-1.5 rounded-lg p-1.5 hover:bg-slate-100 md:hidden dark:hover:bg-slate-800" onClick={() => setOpen(true)}><Icon name="menu" className="size-5" /></button>
          <nav aria-label="Pfad" className="flex min-w-0 items-center gap-1.5 text-sm">
            {group?.title && <><span className="hidden text-slate-400 sm:inline">{group.title}</span><Icon name="chevron" className="hidden size-3.5 text-slate-300 sm:block dark:text-slate-600" /></>}
            <span className="truncate font-medium text-slate-900 dark:text-white">{current?.label ?? (pathname.startsWith("/erfassen") ? "Beleg erfassen" : "OpenEmployed")}</span>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <Link href="/erfassen" aria-label="Beleg fotografieren" title="Beleg fotografieren" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"><Icon name="camera" /></Link>
            <button type="button" aria-label="Suchen" onClick={() => setPalette(true)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 md:hidden dark:hover:bg-slate-800"><Icon name="search" /></button>
          </div>
        </header>
        <main className={`min-w-0 flex-1 px-4 py-7 sm:px-6 md:py-10 ${pathname.startsWith("/projekte") ? "md:px-6" : "md:px-10"}`}>
          {/* Boards (Kanban) nutzen die volle Breite, alle anderen Seiten bleiben gut lesbar begrenzt */}
          <div className={`mx-auto ${pathname.startsWith("/projekte") ? "max-w-none" : "max-w-[1200px]"}`}>{children}</div>
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
    </FeedbackProvider>
  );
}
