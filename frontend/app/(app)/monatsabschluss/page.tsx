"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "@/components/Feedback";
import { Icon } from "@/components/Icon";
import { Alert, Badge, Button, Card, PageHeader, PageSkeleton, Select } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface Item { key: string; label: string; ok: boolean; required: boolean; hint: string; href: string }
interface Check { period: string; items: Item[]; locked: boolean; closable: boolean; closed: { closed_by: string; closed_at: string; confirmed: string[] } | null; done: number; total: number }

const label = (p: string) => new Date(`${p}-01T00:00:00`).toLocaleDateString("de-DE", { month: "long", year: "numeric" });
const lastMonth = () => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7); };

export default function MonthClosePage() {
  const [period, setPeriod] = useState(lastMonth());
  const [data, setData] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<Check>("GET", `/api/reports/month/${period}`).then(setData); }, [period]);
  useEffect(() => { setData(null); load(); }, [load]);
  const months = Array.from({ length: 13 }, (_, i) => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - i); return d.toISOString().slice(0, 7); });

  const close = async () => {
    if (!data) return;
    const missing = data.items.filter((i) => i.required && !i.ok);
    if (!(await confirm({ title: `${label(period)} abschließen und festschreiben?`, tone: missing.length ? "danger" : "primary", confirmLabel: missing.length ? "Trotzdem festschreiben" : "Festschreiben",
      message: missing.length ? <>Noch offen: {missing.map((m) => m.label).join(", ")}. Die Bestätigung wird mit festgeschrieben. Spätere Vorgänge landen im nächsten offenen Monat.</>
        : "Alle Buchungen des Monats werden unveränderlich (GoBD). Spätere Vorgänge landen im nächsten offenen Monat." }))) return;
    setBusy(true);
    try { setData(await api<Check>("POST", `/api/reports/month/${period}/close`, { confirm_open: missing.length > 0 })); toast(`${label(period)} festgeschrieben.`); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Abschluss fehlgeschlagen", "error"); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="Monatsabschluss" eyebrow="Finanzen" description="Schritt für Schritt durch den Monat: Bank, Belege, Rechnungen, Lohn und Steuern – danach wird der Monat festgeschrieben."
        actions={<Select aria-label="Monat" value={period} onChange={(e) => setPeriod(e.target.value)} className="!w-48">{months.map((m) => <option key={m} value={m}>{label(m)}</option>)}</Select>} />
      {!data ? <PageSkeleton stats={0} rows={6} /> : (
        <div className="grid max-w-3xl gap-4">
          {data.locked && <Alert kind="success">{label(period)} ist festgeschrieben{data.closed ? ` – am ${formatDate(data.closed.closed_at)} von ${data.closed.closed_by}` : ""}.{data.closed?.confirmed.length ? ` Bestätigt offen: ${data.closed.confirmed.join("; ")}` : ""}</Alert>}
          <Card className="!p-0">
            <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-4 sm:px-6 dark:border-white/5">
              <span className="font-semibold">{data.done} von {data.total} erledigt</span>
              <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10"><div className="h-full rounded-full bg-brand-500" style={{ width: `${(data.done / data.total) * 100}%` }} /></div>
            </div>
            <ol className="divide-y divide-slate-100 dark:divide-white/5">
              {data.items.map((i, n) => (
                <li key={i.key} className="flex items-start gap-4 px-5 py-4 sm:px-6">
                  <span className={`mt-0.5 grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold ${i.ok ? "bg-emerald-500 text-white" : "bg-slate-100 text-slate-500 dark:bg-white/10"}`}>{i.ok ? <Icon name="check" className="size-3.5" /> : n + 1}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2 font-medium">{i.label}{!i.required && <Badge>optional</Badge>}</span>
                    <span className="block text-sm text-slate-500">{i.hint}</span>
                  </span>
                  {!i.ok && !data.locked && <Link href={i.href} className="shrink-0 text-sm font-medium text-brand-600 hover:underline dark:text-brand-300">Erledigen →</Link>}
                </li>
              ))}
            </ol>
          </Card>
          {!data.locked && (
            <div className="flex flex-wrap items-center justify-end gap-3">
              {!data.closable && <span className="text-sm text-slate-500">Abschluss ab dem 1. des Folgemonats möglich.</span>}
              <Button variant="primary" disabled={busy || !data.closable} onClick={() => void close()}>Monat festschreiben</Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
