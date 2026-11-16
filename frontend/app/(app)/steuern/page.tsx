"use client";

import { ElsterPanel } from "@/components/ElsterPanel";
import { Prepayments, ZmPanel } from "@/components/TaxExtras";
import { useFeedback } from "@/components/Feedback";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, PageHeader, Select } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { formatDate, HttpError } from "@/lib/http";
import { getCalendar, getOverview, getVat, lockVat, monthLabel, periodLabel, type Calendar, type Overview, type VatReturn } from "@/lib/tax";

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "red" | "green" }) {
  const color = tone === "red" ? "text-red-600 dark:text-red-400" : tone === "green" ? "text-emerald-600 dark:text-emerald-400" : "";
  return (
    <Card>
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular ${color}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </Card>
  );
}

export default function TaxPage() {
  const fb = useFeedback();
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [calendar, setCalendar] = useState<Calendar | null>(null);
  const [spec, setSpec] = useState<string>("");
  const [vat, setVat] = useState<VatReturn | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setNotice = (m: string | null) => { if (m) fb.toast(m); };
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    getOverview(year).then(setOverview).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
    getCalendar(year).then(setCalendar).catch(() => undefined);
  }, [year]);
  useEffect(load, [load]);

  const periods = useMemo(() => (calendar?.deadlines ?? []).filter((d) => d.kind === "ustva" && d.period).map((d) => d.period as string), [calendar]);

  useEffect(() => {
    if (periods.length && !periods.includes(spec)) {
      // Standard: ältester offener Zeitraum mit Buchungen – sonst der laufende Zeitraum
      const now = new Date();
      const m = now.getMonth() + 1;
      const current = calendar?.filing === "monthly" ? `${now.getFullYear()}-${String(m).padStart(2, "0")}` : `${now.getFullYear()}-Q${Math.ceil(m / 3)}`;
      const ustva = (calendar?.deadlines ?? []).filter((d) => d.kind === "ustva");
      const candidate = ustva.find((d) => d.active && !d.done)?.period ?? (periods.includes(current) ? current : periods[periods.length - 1]);
      setSpec(candidate ?? "");
    }
  }, [periods, spec, calendar]);

  useEffect(() => {
    if (!spec) return;
    getVat(spec).then(setVat).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, [spec]);

  async function lock() {
    if (!vat) return;
    if (!await fb.confirm(`UStVA ${periodLabel(vat.period)} festschreiben und Zeitraum sperren?\n\nZahllast: ${formatEuro(vat.payable)}\n\nSpätere Belege mit Datum in diesem Zeitraum werden automatisch dem nächsten offenen Monat zugeordnet.`)) return;
    setBusy(true);
    setError(null);
    try {
      let res: { ledger_hash: string };
      try {
        res = await lockVat(vat.period);
      } catch (first) {
        const items = first instanceof HttpError && first.status === 409 ? (first.detail as { open_items?: { text: string }[] } | null)?.open_items : undefined;
        if (!items) throw first;
        const ok = await fb.confirm(`Vor der Festschreibung sind noch Punkte offen:\n\n${items.map((i) => `• ${i.text}`).join("\n")}\n\nTrotzdem festschreiben? Die Bestätigung wird mit festgeschrieben; spätere Vorgänge landen in der nächsten offenen Periode.`);
        if (!ok) return;
        res = await lockVat(vat.period, true);
      }
      setNotice(`Zeitraum gesperrt und festgeschrieben (Hash ${res.ledger_hash.slice(0, 12)}…). Übertragt die Kennzahlen jetzt in Mein ELSTER.`);
      setVat(await getVat(vat.period));
      load();
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Sperren fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const t = overview?.income_tax;
  const vatPayableYtd = overview?.totals.vat_payable ?? "0";

  return (
    <>
      <PageHeader
        title="Steuern"
        description="Gewinn, Steuerrücklagen und Umsatzsteuer-Voranmeldung auf Basis festgeschriebener Rechnungen und gebuchter Belege (Soll-Versteuerung)."
        actions={
          <Select aria-label="Jahr" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28">
            {[thisYear - 1, thisYear, thisYear + 1].map((y) => <option key={y} value={y}>{y}</option>)}
          </Select>
        }
      />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {overview && t && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label={`Umsatz netto ${year}`} value={formatEuro(overview.totals.revenue)} />
            <Stat label="Aufwand" value={formatEuro(overview.totals.expense)} />
            <Stat label="Gewinn vor Steuern" value={formatEuro(t.profit_before_tax)} tone={Number(t.profit_before_tax) < 0 ? "red" : "green"} />
            <Stat label="Zurücklegen (Steuern gesamt)" value={formatEuro(overview.reserve_total)} hint="Ertragsteuern + offene USt-Zahllast des Jahres" />
          </div>

          <div className="mb-6 grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="mb-3 font-medium">Rücklagen (Schätzung {year})</h2>
              <dl className="space-y-1 text-sm tabular">
                <div className="flex justify-between"><dt className="text-slate-500">Umsatzsteuer-Zahllast (Jahr)</dt><dd>{formatEuro(vatPayableYtd)}</dd></div>
                <div className="flex justify-between"><dt className="text-slate-500">Körperschaftsteuer ({t.kst_rate.replace(/\.0+$/, "")} %)</dt><dd>{formatEuro(t.kst)}</dd></div>
                <div className="flex justify-between"><dt className="text-slate-500">Solidaritätszuschlag (5,5 % der KSt)</dt><dd>{formatEuro(t.solz)}</dd></div>
                <div className="flex justify-between"><dt className="text-slate-500">Gewerbesteuer (3,5 % × {t.trade_tax_multiplier} % auf {formatEuro(t.gewst_base)})</dt><dd>{formatEuro(t.gewst)}</dd></div>
                <div className="flex justify-between border-t border-slate-200 pt-1 font-semibold dark:border-slate-700"><dt>Ertragsteuern gesamt</dt><dd>{formatEuro(t.total)}</dd></div>
                <div className="flex justify-between pt-2"><dt className="text-slate-500">Gewinn nach Steuern</dt><dd>{formatEuro(t.profit_after_tax)}</dd></div>
                <div className="flex justify-between"><dt className="text-slate-500">Gesetzliche UG-Rücklage (25 %, bleibt in der Firma)</dt><dd>{formatEuro(t.ug_reserve)}</dd></div>
              </dl>
              <p className="mt-3 text-xs text-slate-500">⚠️ Schätzung ohne Hinzurechnungen, Verlustvorträge und Vorauszahlungen. Steuerlich abziehbarer Aufwand berücksichtigt (z. B. Bewirtung 70 %).</p>
            </Card>

            <Card>
              <h2 className="mb-3 font-medium">Steuerkalender {year}</h2>
              <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">
                {calendar?.deadlines.map((d) => (
                  <li key={`${d.kind}-${d.due}-${d.label}`} className="flex items-center justify-between gap-2">
                    <span><span className="tabular text-slate-500">{formatDate(d.due)}</span> · {d.label}</span>
                    {d.done ? <Badge tone="green">gemeldet</Badge> : d.overdue ? <Badge tone="red">überfällig</Badge> : null}
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-slate-500">Voranmeldung {calendar?.filing === "monthly" ? "monatlich" : "vierteljährlich"}{calendar?.extension ? " mit Dauerfristverlängerung" : ""}; Termine auf Werktage (NRW) verschoben.</p>
            </Card>
          </div>
        </>
      )}

      <Card className="mb-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-medium">Umsatzsteuer-Voranmeldung</h2>
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Zeitraum" value={spec} onChange={(e) => setSpec(e.target.value)} className="w-48">
              {periods.map((p) => <option key={p} value={p}>{periodLabel(p)}</option>)}
            </Select>
            {vat?.locked ? <Badge tone="green">gesperrt</Badge> : <Button variant="primary" onClick={lock} disabled={busy || !vat?.closable} title={vat?.closable ? "" : "Zeitraum noch nicht abgelaufen"}>Festschreiben & sperren</Button>}
          </div>
        </div>
        {vat && (
          <div className="overflow-x-auto"><table className="w-full text-sm tabular">
            <thead className="border-b border-slate-200 text-left text-xs text-slate-500 dark:border-slate-800">
              <tr><th className="pb-2 font-medium">Kz</th><th className="pb-2 font-medium">Bezeichnung</th><th className="pb-2 text-right font-medium">Bemessungsgrundlage</th><th className="pb-2 text-right font-medium">Steuer</th></tr>
            </thead>
            <tbody>
              {vat.lines.map((l) => (
                <Fragment key={l.kz}>
                  <tr className={`border-b border-slate-100 dark:border-slate-800 ${l.kz === "83" ? "font-semibold" : ""}`}>
                    <td className="py-2">{l.kz}</td>
                    <td className="py-2">
                      {l.items.length > 0 ? (
                        <button type="button" className="text-left hover:underline" onClick={() => setOpen(open === l.kz ? null : l.kz)} aria-expanded={open === l.kz}>
                          {l.label} <span className="text-xs text-slate-500">({l.items.length})</span>
                        </button>
                      ) : l.label}
                      {l.rounding_difference && l.rounding_difference !== "0.00" && <div className="text-xs text-slate-500">Formular rechnet aus vollen Euro; Rechnungen exakt {formatEuro(l.tax_exact)}</div>}
                    </td>
                    <td className="py-2 text-right">{l.base !== undefined ? `${l.base.replace(/\B(?=(\d{3})+(?!\d))/g, ".")} €` : ""}</td>
                    <td className="py-2 text-right">{l.tax !== undefined ? formatEuro(l.tax) : ""}</td>
                  </tr>
                  {open === l.kz && l.items.map((i) => (
                    <tr key={`${l.kz}-${i.id}`} className="bg-slate-50 text-xs dark:bg-slate-800/40">
                      <td />
                      <td className="py-1 pl-4">{formatDate(i.date)} · {i.type === "invoice" ? "Rechnung" : "Beleg"} {i.ref}</td>
                      <td className="py-1 text-right">{formatEuro(i.base)}</td>
                      <td className="py-1 text-right">{formatEuro(i.tax)}</td>
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table></div>
        )}
        {vat && <ElsterPanel spec={spec} locked={!!vat.locked} />}
        <p className="mt-3 text-xs text-slate-500">⚠️ Kennzahlen jährlich mit dem amtlichen Vordruck abgleichen; ERiC prüft jeden Datensatz vor der Übermittlung.</p>
      </Card>

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <Prepayments year={year} />
        <ZmPanel year={year} />
      </div>

      {overview && (
        <Card>
          <h2 className="mb-3 font-medium">Monatsübersicht {year}</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm tabular">
              <thead className="border-b border-slate-200 text-left text-xs text-slate-500 dark:border-slate-800">
                <tr><th className="pb-2 font-medium">Monat</th><th className="pb-2 text-right font-medium">Umsatz netto</th><th className="pb-2 text-right font-medium">Aufwand</th><th className="pb-2 text-right font-medium">Gewinn</th><th className="pb-2 text-right font-medium">USt</th><th className="pb-2 text-right font-medium">Vorsteuer</th><th className="pb-2 text-right font-medium">Zahllast</th></tr>
              </thead>
              <tbody>
                {overview.months.map((m) => (
                  <tr key={m.month} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="py-1.5">{monthLabel(m.month)}</td>
                    <td className="py-1.5 text-right">{formatEuro(m.revenue)}</td>
                    <td className="py-1.5 text-right">{formatEuro(m.expense)}</td>
                    <td className="py-1.5 text-right">{formatEuro(m.profit)}</td>
                    <td className="py-1.5 text-right">{formatEuro((Number(m.output_tax) + Number(m.rc_tax)).toFixed(2))}</td>
                    <td className="py-1.5 text-right">{formatEuro(m.input_tax)}</td>
                    <td className="py-1.5 text-right">{formatEuro(m.vat_payable)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
