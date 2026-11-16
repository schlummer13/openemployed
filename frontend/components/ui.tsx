"use client";

import { useEffect, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { Icon, type IconName } from "./Icon";

export const inputClass =
  "h-9 min-w-0 rounded-lg border-0 bg-white px-3 text-sm text-slate-900 shadow-[0_0_0_1px_rgb(15_15_25/0.12),0_1px_2px_rgb(15_15_25/0.05)] outline-none transition " +
  "placeholder:text-slate-400 hover:shadow-[0_0_0_1px_rgb(15_15_25/0.2),0_1px_2px_rgb(15_15_25/0.05)] focus:shadow-[0_0_0_1px_var(--color-brand-500),0_0_0_4px_rgb(18_160_147/0.15)] " +
  "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500 " +
  "dark:bg-slate-900 dark:text-slate-100 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.1)] dark:hover:shadow-[0_0_0_1px_rgb(255_255_255/0.18)] dark:focus:shadow-[0_0_0_1px_var(--color-brand-400),0_0_0_4px_rgb(18_160_147/0.25)] dark:disabled:bg-slate-850";

export function PageHeader({ title, description, actions, eyebrow }: { title: string; description?: string; actions?: ReactNode; eyebrow?: string }) {
  return (
    <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
      <div className="min-w-0 max-w-3xl flex-1">
        {eyebrow && <p className="mb-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-brand-600 dark:text-brand-400">{eyebrow}</p>}
        <h1 className="text-[28px] font-semibold leading-[1.15] tracking-[-0.025em] text-slate-950 dark:text-white">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-[14.5px] leading-relaxed text-slate-500 dark:text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl bg-white p-5 shadow-card sm:p-6 dark:bg-slate-900 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.07)] ${className}`}>
      {children}
    </section>
  );
}

export function CardHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-slate-500 dark:text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, icon, tone = "slate", trend }: {
  label: string; value: ReactNode; hint?: ReactNode; icon?: IconName; tone?: "slate" | "green" | "amber" | "red" | "brand";
  /** Veränderung zum Vormonat in Prozent; good = ob ein Anstieg positiv ist (Einnahmen) oder negativ (Ausgaben) */
  trend?: { pct: number | null; good: "up" | "down" };
}) {
  const tones = {
    slate: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
    green: "bg-emerald-50 text-emerald-600 ring-emerald-600/10 dark:bg-emerald-500/10 dark:text-emerald-400",
    amber: "bg-amber-50 text-amber-600 ring-amber-600/10 dark:bg-amber-500/10 dark:text-amber-400",
    red: "bg-red-50 text-red-600 ring-red-600/10 dark:bg-red-500/10 dark:text-red-400",
    brand: "bg-brand-50 text-brand-600 ring-brand-600/10 dark:bg-brand-500/10 dark:text-brand-300",
  }[tone];
  return (
    <Card className="relative overflow-hidden">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-[13px] font-medium leading-snug text-slate-500 dark:text-slate-400">{label}</p>
        {icon && <span className={`grid size-8 shrink-0 place-items-center rounded-lg ring-1 ring-inset ring-transparent ${tones}`}><Icon name={icon} /></span>}
      </div>
      <p className="mt-3 truncate text-[26px] font-semibold leading-none tracking-[-0.02em] tabular text-slate-950 dark:text-white">{value}</p>
      {trend && trend.pct !== null && Number.isFinite(trend.pct) && (
        <p className="mt-2 flex items-center gap-1.5 text-xs">
          <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 font-medium tabular ${
            Math.abs(trend.pct) < 0.5 ? "bg-slate-100 text-slate-600 dark:bg-white/5 dark:text-slate-300"
              : (trend.pct > 0) === (trend.good === "up") ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
                : "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-300"}`}>
            {trend.pct > 0 ? "▲" : trend.pct < 0 ? "▼" : "■"} {Math.abs(trend.pct).toLocaleString("de-DE", { maximumFractionDigits: 0 })} %
          </span>
          <span className="text-slate-500 dark:text-slate-400">ggü. Vormonat</span>
        </p>
      )}
      {hint && <p className="mt-2 text-xs leading-snug text-slate-500 dark:text-slate-400">{hint}</p>}
    </Card>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const BASE = "inline-flex h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm font-medium transition-[background-color,box-shadow,color,transform] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand-600 text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.15),0_1px_2px_rgb(33_26_76/0.3),0_0_0_1px_var(--color-brand-700)] hover:bg-brand-700",
  secondary: "bg-white text-slate-800 shadow-[0_0_0_1px_rgb(15_15_25/0.12),0_1px_2px_rgb(15_15_25/0.05)] hover:bg-slate-50 hover:text-slate-950 dark:bg-slate-850 dark:text-slate-100 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.1)] dark:hover:bg-slate-800",
  danger: "bg-red-600 text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.15),0_0_0_1px_rgb(185_28_28)] hover:bg-red-700",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return <button type="button" {...props} className={`${BASE} ${VARIANTS[variant]} ${className}`} />;
}

export const linkButtonClass = `${BASE} ${VARIANTS.secondary}`;
export const primaryLinkClass = `${BASE} ${VARIANTS.primary}`;

export function Field({ label, hint, children, className = "" }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block text-sm ${className}`}>
      <span className="mb-1.5 block text-[13px] font-medium leading-snug text-slate-700 dark:text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

/** Volle Breite, außer der Aufrufer gibt selbst eine Breite vor (w-28, w-44 …) – sonst gewinnt w-full je nach CSS-Reihenfolge. */
const width = (cls?: string) => (/(^|\s)(!?w-|max-w-|flex-1)/.test(cls ?? "") ? "" : "w-full");

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputClass} ${width(props.className)} ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputClass} ${width(props.className)} pr-8 ${props.className ?? ""}`} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea rows={3} {...props} className={`${inputClass} ${width(props.className)} h-auto py-2 leading-relaxed ${props.className ?? ""}`} />;
}

export function Alert({ kind = "error", children }: { kind?: "error" | "warning" | "success" | "info"; children: ReactNode }) {
  const styles = {
    error: "border-red-200 bg-red-50 text-red-800 dark:border-red-900/60 dark:bg-red-950/50 dark:text-red-200",
    warning: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/50 dark:text-amber-100",
    success: "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-950/50 dark:text-emerald-100",
    info: "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  }[kind];
  const dot = { error: "bg-red-500", warning: "bg-amber-500", success: "bg-emerald-500", info: "bg-brand-500" }[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`flex gap-2.5 rounded-xl border px-4 py-3 text-sm leading-relaxed ${styles}`}>
      <span className={`mt-[7px] size-1.5 shrink-0 rounded-full ${dot} ring-4 ring-current/10`} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "amber" | "red" | "blue" }) {
  const tones = {
    slate: "bg-slate-100 text-slate-700 ring-slate-500/15 dark:bg-white/5 dark:text-slate-300 dark:ring-white/10",
    green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/20",
    amber: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-400/20",
    red: "bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-400/20",
    blue: "bg-brand-50 text-brand-700 ring-brand-600/20 dark:bg-brand-500/10 dark:text-brand-200 dark:ring-brand-400/20",
  }[tone];
  const dot = { slate: "bg-slate-400", green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-red-500", blue: "bg-brand-500" }[tone];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tones}`}>
      <span className={`size-1.5 rounded-full ${dot}`} />{children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-4 py-12 text-center text-sm text-slate-500 dark:text-slate-400">
      <span className="grid size-11 place-items-center rounded-xl bg-slate-50 text-slate-400 shadow-card dark:bg-slate-800"><Icon name="inbox" className="size-5" /></span>
      <div className="max-w-sm leading-relaxed">{children}</div>
    </div>
  );
}

export function Table({ head, children }: { head: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLTableElement>(null);
  // Spaltennamen für die Kartenansicht auf dem Handy an jede Zelle schreiben (auch nach Änderungen der Zeilen)
  useEffect(() => {
    const table = ref.current;
    if (!table) return;
    const label = () => {
      const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent?.trim() ?? "");
      table.querySelectorAll("tbody tr").forEach((tr) => {
        [...tr.children].forEach((td, i) => { const l = heads[i] ?? ""; if (td.getAttribute("data-label") !== l) td.setAttribute("data-label", l); });
      });
    };
    label();
    const obs = new MutationObserver(label);
    obs.observe(table, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, []);
  return (
    <div className="-mx-5 overflow-x-auto sm:-mx-6">
      <table ref={ref} className="responsive-table w-full text-sm">
        <thead className="border-y border-slate-100 bg-slate-25 text-left text-xs font-medium text-slate-500 dark:border-slate-800 dark:bg-white/[0.02] dark:text-slate-400 [&_th]:whitespace-nowrap [&_th]:py-2.5 [&_th]:pr-4 [&_th:first-child]:pl-5 sm:[&_th:first-child]:pl-6 [&_th:last-child]:pr-5 sm:[&_th:last-child]:pr-6">{head}</thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800 [&_tr]:transition-colors [&_tr:hover]:bg-slate-25 dark:[&_tr:hover]:bg-white/[0.02] [&_td]:py-3 [&_td]:pr-4 [&_td]:align-middle [&_td:first-child]:pl-5 sm:[&_td:first-child]:pl-6 [&_td:last-child]:pr-5 sm:[&_td:last-child]:pr-6">{children}</tbody>
      </table>
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[] }) {
  return (
    <div role="tablist" className="mb-6 inline-flex max-w-full gap-1 overflow-x-auto rounded-xl bg-slate-100 p-1 dark:bg-slate-900">
      {items.map((t) => (
        <button key={t.value} role="tab" type="button" aria-selected={value === t.value} onClick={() => onChange(t.value)}
          className={`whitespace-nowrap rounded-lg px-3.5 py-1.5 text-sm font-medium transition ${value === t.value
            ? "bg-white text-slate-950 shadow-card dark:bg-slate-800 dark:text-white"
            : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"}`}>{t.label}</button>
      ))}
    </div>
  );
}

/** Seitenpanel von rechts: Bearbeiten, ohne die Liste zu verlassen. Esc oder Klick daneben schließt. */
export function Drawer({ open, onClose, title, subtitle, children, footer, size = "md" }: {
  open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode;
  size?: "md" | "lg" | "xl";
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);
  if (!open) return null;
  const width = { md: "max-w-lg", lg: "max-w-3xl", xl: "max-w-6xl" }[size];
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}>
      <div className="absolute inset-0 bg-slate-950/30 backdrop-blur-[2px]" onClick={onClose} />
      <aside className={`absolute inset-y-0 right-0 flex w-full ${width} animate-drawer flex-col bg-white shadow-pop dark:bg-slate-900`}>
        <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4 sm:px-6 dark:border-white/5">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{title}</h2>
            {subtitle && <p className="mt-0.5 text-[13px] text-slate-500 dark:text-slate-400">{subtitle}</p>}
          </div>
          <button type="button" aria-label="Schließen" onClick={onClose} className="-mr-1.5 rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/10 dark:hover:text-white">
            <Icon name="x" className="size-5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">{children}</div>
        {footer && <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 bg-slate-25 px-5 py-3 sm:px-6 dark:border-white/5 dark:bg-white/[0.02]">{footer}</footer>}
      </aside>
    </div>
  );
}

/** Grauer Platzhalter während des Ladens */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-lg bg-slate-200/70 dark:bg-white/[0.06] ${className}`} />;
}

export function PageSkeleton({ stats = 4, rows = 6 }: { stats?: number; rows?: number }) {
  return (
    <div role="status" aria-label="Lädt …" className="space-y-6">
      <div className="space-y-2"><Skeleton className="h-8 w-56" /><Skeleton className="h-4 w-96 max-w-full" /></div>
      {stats > 0 && <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: stats }, (_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}</div>}
      <div className="space-y-3 rounded-2xl bg-white p-6 shadow-card dark:bg-slate-900">
        {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-5" />)}
      </div>
    </div>
  );
}
