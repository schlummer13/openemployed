"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/http";
import { useUser } from "./AuthGate";
import { Icon, type IconName } from "./Icon";

export interface NavItem { label: string; href: string; icon: IconName; area?: string; write?: boolean }

export const NAV_GROUPS: { title: string | null; items: NavItem[] }[] = [
  { title: null, items: [
    { label: "Übersicht", href: "/", icon: "home", area: "dashboard" }, { label: "Zu erledigen", href: "/aufgaben", icon: "list", area: "dashboard" },
    { label: "Stempeluhr", href: "/stempeluhr", icon: "clock" }, { label: "Abwesenheiten", href: "/abwesenheit", icon: "plane" },
  ] },
  { title: "Vertrieb", items: [
    { label: "Pipeline", href: "/vertrieb", icon: "kanban", area: "sales" },
    { label: "Kunden", href: "/kunden", icon: "users", area: "sales" },
    { label: "Produkte", href: "/produkte", icon: "box", area: "sales" },
    { label: "Angebote", href: "/angebote", icon: "send", area: "sales" },
    { label: "Verträge", href: "/vertraege", icon: "repeat", area: "sales" },
  ] },
  { title: "Finanzen", items: [
    { label: "Rechnungen", href: "/rechnungen", icon: "file", area: "invoices" },
    { label: "Forderungen", href: "/forderungen", icon: "alarm", area: "invoices" },
    { label: "Belege", href: "/belege", icon: "receipt", area: "receipts" },
    { label: "Budgets", href: "/budgets", icon: "pie", area: "accounting" },
    { label: "Anlagen", href: "/anlagen", icon: "layers", area: "accounting" },
    { label: "Zahlungsverkehr", href: "/zahlungsverkehr", icon: "wallet", area: "payments" },
    { label: "Buchhaltung", href: "/buchhaltung", icon: "bank", area: "accounting" },
    { label: "Auswertungen", href: "/auswertungen", icon: "trend", area: "reports" },
    { label: "Monatsabschluss", href: "/monatsabschluss", icon: "check", area: "reports" },
    { label: "Steuern", href: "/steuern", icon: "percent", area: "taxes" },
    { label: "Jahresabschluss", href: "/abschluss", icon: "scale", area: "reports" },
  ] },
  { title: "Team", items: [
    { label: "Mitarbeiter & Lohn", href: "/mitarbeiter", icon: "users", area: "payroll" },
    { label: "Team-Kalender", href: "/abwesenheit/team", icon: "list", area: "payroll" },
    { label: "Feiertage", href: "/feiertage", icon: "sun" },
    { label: "GF-Gehälter", href: "/gehaelter", icon: "wallet", area: "payroll" },
    { label: "Projekte", href: "/projekte", icon: "kanban", area: "projects" },
    { label: "Zeiterfassung", href: "/zeiten", icon: "clock", area: "projects" },
    { label: "Reisekosten", href: "/reisekosten", icon: "plane", area: "projects" },
  ] },
  { title: "System", items: [
    { label: "Automatisierungen", href: "/automatisierungen", icon: "zap", area: "automations" },
    { label: "Firma", href: "/einstellungen/firma", icon: "building", area: "admin" },
    { label: "Dokumentenarchiv", href: "/archiv", icon: "layers", area: "archive" },
    { label: "Umstieg & Import", href: "/import", icon: "inbox", area: "accounting", write: true },
    { label: "Einrichtung", href: "/einrichtung", icon: "zap", area: "admin", write: true },
    { label: "Benutzer & Rollen", href: "/einstellungen/benutzer", icon: "users", area: "admin", write: true },
    { label: "System & Integrationen", href: "/einstellungen/system", icon: "shield", area: "admin", write: true },
    { label: "Mein Konto", href: "/einstellungen/konto", icon: "shield" },
    { label: "Handbuch", href: "/handbuch", icon: "file" },
    { label: "Entwickler-API", href: "/entwickler", icon: "command", area: "admin", write: true },
  ] },
];

export const NAV: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

export function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const me = useUser();
  const perms = me?.permissions ?? {};
  const role = me?.role ?? "admin";
  const visible = (item: NavItem) => !item.area || (item.write ? perms[item.area] === "write" : (perms[item.area] ?? "none") !== "none");
  const groups = NAV_GROUPS.map((g) => ({ ...g, items: g.items.filter(visible) })).filter((g) => g.items.length);
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  // Zähler für „Zu erledigen“ (dringende Punkte), bei jedem Seitenwechsel aktualisiert
  const [urgent, setUrgent] = useState<{ red: number; all: number } | null>(null);
  useEffect(() => {
    if ((perms.dashboard ?? "none") === "none") return;
    void api<{ tasks: { severity: string }[] }>("GET", "/api/inbox").then((r) => setUrgent({ red: r.tasks.filter((t) => t.severity === "red").length, all: r.tasks.length })).catch(() => undefined);
  }, [pathname, role]);
  return (
    <nav className="-mx-1 flex-1 space-y-6 overflow-y-auto px-1 text-[13.5px]" aria-label="Hauptnavigation">
      {groups.map((g) => (
        <div key={g.title ?? "start"}>
          {g.title && <p className="mb-1 px-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400 dark:text-slate-500">{g.title}</p>}
          <ul className="space-y-0.5">
            {g.items.map((item) => {
              const active = isActive(item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={`group relative flex h-8 items-center gap-2.5 rounded-lg px-2.5 transition-colors ${active
                      ? "bg-white font-medium text-slate-950 shadow-card dark:bg-white/[0.06] dark:text-white dark:shadow-none"
                      : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-950 dark:text-slate-400 dark:hover:bg-white/[0.04] dark:hover:text-slate-100"}`}
                  >
                    {active && <span className="absolute -left-3 top-1.5 h-5 w-[3px] rounded-r-full bg-brand-600 dark:bg-brand-400" />}
                    <Icon name={item.icon} className={`size-4 shrink-0 ${active ? "text-brand-600 dark:text-brand-400" : "text-slate-400 group-hover:text-slate-600 dark:group-hover:text-slate-300"}`} />
                    <span className="truncate">{item.label}</span>
                    {item.href === "/aufgaben" && urgent && urgent.all > 0 && (
                      <span className={`ml-auto rounded-full px-1.5 py-px text-[11px] font-semibold tabular ${urgent.red ? "bg-red-500 text-white" : "bg-slate-200 text-slate-600 dark:bg-white/10 dark:text-slate-300"}`}>{urgent.all}</span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
