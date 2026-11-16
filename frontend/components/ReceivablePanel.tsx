"use client";

import { useFeedback } from "@/components/Feedback";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Alert, Badge, Button, Card, Field, Input, Select } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, formatDate, HttpError, todayIso } from "@/lib/http";

interface Payment { id: string; paid_on: string; amount: string; method: string; reference: string | null; source: string; created_by: string; reversed_at: string | null; reversal_reason: string | null }
interface Notice { id: string; number: string; level: number; notice_date: string; deadline: string; interest: string; fee: string; total: string; sent_at: string | null; sent_to: string | null }
export interface Receivable {
  status: "open" | "partial" | "overdue" | "paid" | "costs_open" | "cancelled";
  gross: string; paid: string; principal_open: string; interest_open: string; costs_open: string; total_open: string;
  default_from: string | null; days_overdue: number; level: number;
  payments: Payment[]; notices: Notice[]; block: { reason: string; until: string | null } | null; rate_missing: string | null;
}

export const RECEIVABLE_STATUS: Record<Receivable["status"], [string, "slate" | "green" | "amber" | "red" | "blue"]> = {
  open: ["offen", "blue"], partial: ["teilbezahlt", "amber"], overdue: ["überfällig", "red"], paid: ["bezahlt", "green"],
  costs_open: ["Kosten offen", "amber"], cancelled: ["storniert", "slate"],
};
const LEVEL = ["", "Zahlungserinnerung", "Mahnung", "Letzte Mahnung"];
const METHOD: Record<string, string> = { bank: "Überweisung", cash: "bar", offset: "Verrechnung", other: "sonstige" };
const linkClass = "rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800";

export function ReceivablePanel({ invoiceId }: { invoiceId: string }) {
  const fb = useFeedback();
  const [r, setR] = useState<Receivable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pay, setPay] = useState({ paid_on: todayIso(), amount: "", method: "bank", reference: "" });
  const [blockReason, setBlockReason] = useState("");

  const load = useCallback(() => { void api<Receivable>("GET", `/api/invoices/${invoiceId}/receivable`).then(setR).catch(() => setR(null)); }, [invoiceId]);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); } finally { setBusy(false); }
  }
  if (!r) return null;
  const [label, tone] = RECEIVABLE_STATUS[r.status];
  const lastUnsent = r.notices.find((n) => !n.sent_at);

  const addPayment = (e: FormEvent) => { e.preventDefault(); void act(async () => {
    await api("POST", `/api/invoices/${invoiceId}/payments`, { ...pay, amount: toApiDecimal(pay.amount), reference: pay.reference || null });
    setPay({ ...pay, amount: "", reference: "" });
  }); };
  const reverse = (p: Payment) => act(async () => {
    const reason = await fb.prompt("Grund der Stornierung (z. B. Rücklastschrift, falsch zugeordnet):");
    if (!reason) return;
    await api("POST", `/api/payments/${p.id}/reverse`, { reason });
  });

  return (
    <Card className="mt-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-slate-500">Zahlungen &amp; Mahnungen</h2>
        <Badge tone={tone}>{label}{r.status === "overdue" ? ` · ${r.days_overdue} Tage` : ""}</Badge>
      </div>
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}
      {r.status !== "cancelled" && (
        <dl className="mb-4 grid gap-2 text-sm tabular sm:grid-cols-5">
          <div><dt className="text-xs text-slate-500">Rechnungsbetrag</dt><dd>{formatEuro(r.gross)}</dd></div>
          <div><dt className="text-xs text-slate-500">Gezahlt</dt><dd>{formatEuro(r.paid)}</dd></div>
          <div><dt className="text-xs text-slate-500">Zinsen offen</dt><dd>{formatEuro(r.interest_open)}</dd></div>
          <div><dt className="text-xs text-slate-500">Kosten offen</dt><dd>{formatEuro(r.costs_open)}</dd></div>
          <div><dt className="text-xs text-slate-500">Gesamt offen</dt><dd className="font-semibold">{formatEuro(r.total_open)}</dd></div>
        </dl>
      )}
      {r.default_from && r.status !== "paid" && r.status !== "cancelled" && (
        <p className="mb-3 text-xs text-slate-500">Verzug ab {formatDate(r.default_from)} (§ 286 BGB) – Zinsen laufen bis zum Zahlungseingang.</p>
      )}

      {r.payments.length > 0 && (
        <ul className="mb-4 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {r.payments.map((p) => (
            <li key={p.id} className={`flex flex-wrap items-center justify-between gap-2 py-1.5 ${p.reversed_at ? "text-slate-400 line-through" : ""}`}>
              <span>{formatDate(p.paid_on)} · {METHOD[p.method] ?? p.method}{p.reference ? ` · ${p.reference}` : ""}{p.source !== "manual" ? ` · ${p.source}` : ""}</span>
              <span className="flex items-center gap-2 tabular">{formatEuro(p.amount)}
                {!p.reversed_at && <button className="text-xs text-slate-500 no-underline hover:text-red-600" onClick={() => reverse(p)} disabled={busy}>stornieren</button>}
                {p.reversed_at && <span className="text-xs no-underline" title={p.reversal_reason ?? ""}>storniert</span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {r.status !== "paid" && r.status !== "cancelled" && (
        <form onSubmit={addPayment} className="mb-4 grid items-end gap-2 sm:grid-cols-[9rem_8rem_9rem_1fr_auto]">
          <Field label="Zahlungsdatum"><Input type="date" required max={todayIso()} value={pay.paid_on} onChange={(e) => setPay({ ...pay, paid_on: e.target.value })} /></Field>
          <Field label="Betrag (€)"><Input required inputMode="decimal" placeholder={r.total_open.replace(".", ",")} value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
          <Field label="Art"><Select value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>{Object.entries(METHOD).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
          <Field label="Verwendungszweck / Notiz"><Input value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} /></Field>
          <Button type="submit" variant="primary" disabled={busy || !pay.amount}>Zahlung erfassen</Button>
        </form>
      )}

      {r.notices.length > 0 && (
        <ul className="mb-3 space-y-1 text-sm">
          {r.notices.map((n) => (
            <li key={n.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>{LEVEL[n.level]} {n.number} vom {formatDate(n.notice_date)} · Frist {formatDate(n.deadline)} · {formatEuro(n.total)}
                {Number(n.interest) > 0 && <span className="text-xs text-slate-500"> (inkl. {formatEuro(n.interest)} Zinsen{Number(n.fee) > 0 ? `, ${formatEuro(n.fee)} Kosten` : ""})</span>}
              </span>
              <span className="flex gap-2">
                <a className={linkClass} href={`/api/dunning/${n.id}/pdf`} target="_blank" rel="noopener">PDF</a>
                {n.sent_at ? <span className="text-xs text-slate-500">versendet {formatDate(n.sent_at.slice(0, 10))} an {n.sent_to}</span> : (
                  <>
                    <Button className="!px-2 !h-7 !py-0 !text-xs" onClick={() => act(() => api("POST", `/api/dunning/${n.id}/send`))} disabled={busy}>Per E-Mail senden</Button>
                    <Button className="!px-2 !h-7 !py-0 !text-xs" onClick={() => act(() => api("POST", `/api/dunning/${n.id}/mark-sent`, { channel: "post" }))} disabled={busy}>Per Post versendet</Button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {(r.status === "overdue" || r.status === "costs_open") && (
        <div className="flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          {r.block ? (
            <>
              <span className="text-sm text-amber-700 dark:text-amber-300">Mahnsperre: {r.block.reason}{r.block.until ? ` (bis ${formatDate(r.block.until)})` : ""}</span>
              <Button onClick={() => act(() => api("PUT", `/api/invoices/${invoiceId}/dunning-block`, {}))} disabled={busy}>Sperre aufheben</Button>
            </>
          ) : (
            <>
              {r.level < 3 && <Button variant="primary" disabled={busy || !!lastUnsent} title={lastUnsent ? "Erst das letzte Schreiben versenden" : ""} onClick={() => act(() => api("POST", `/api/invoices/${invoiceId}/dunning`))}>{LEVEL[r.level + 1]} erstellen</Button>}
              <Input aria-label="Grund für Mahnsperre" placeholder="Grund für Mahnsperre …" className="w-56" value={blockReason} onChange={(e) => setBlockReason(e.target.value)} />
              <Button disabled={busy || blockReason.trim().length < 3} onClick={() => act(async () => { await api("PUT", `/api/invoices/${invoiceId}/dunning-block`, { reason: blockReason }); setBlockReason(""); })}>Mahnsperre setzen</Button>
            </>
          )}
          {r.level >= 3 && !r.block && <span className="text-sm text-red-700 dark:text-red-300">Alle Mahnstufen ausgeschöpft – gerichtliches Mahnverfahren (online-mahnantrag.de) oder Inkasso prüfen.</span>}
        </div>
      )}
      {r.rate_missing && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">Basiszinssatz ab {formatDate(r.rate_missing)} fehlt – bitte unter Forderungen eintragen (sonst keine Mahnung mit Zinsen).</p>}
    </Card>
  );
}
