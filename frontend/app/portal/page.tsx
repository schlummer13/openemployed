"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { PublicShell } from "@/components/PublicShell";
import { EmployeeArea } from "./EmployeeArea";
import { Alert, Badge, Button, Card, Field, Input, Textarea } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { formatDate, HttpError, publicApi } from "@/lib/http";

interface CustomerRef { id: string; number: string; name: string; customer_type: string }
interface Me { email: string; customer_id: string | null; customers: CustomerRef[]; employee: { name: string } | null }
interface Overview {
  customer: { id: string; number: string; type: "b2b" | "b2c"; name: string; email: string; street: string; postal_code: string; city: string };
  company: { name: string; email: string | null; phone: string | null; iban: string | null; bic: string | null; bank_name: string | null };
  invoices: { id: string; kind: string; number: string; issue_date: string; due_date: string; total_gross: string; status: string; total_open: string }[];
  offers: { id: string; number: string; title: string; status: string; valid_until: string; total_net: string; total_gross: string; price_mode: string; expired: boolean }[];
  contracts: { id: string; number: string; title: string; status: string; start_date: string; end_date: string | null; notice_received: string | null }[];
  notices: { id: string; number: string; level: number; notice_date: string; deadline: string; total: string; invoice_number: string }[];
  terminations: { number: string; kind: string; contract_reference: string | null; received_at: string; status: string; effective_date: string | null }[];
  total_open: string;
}
interface Payment { number: string; amount: string; recipient: string; iban: string; bic: string | null; reference: string; girocode: string | null }

const INV_STATUS: Record<string, [string, "slate" | "green" | "amber" | "red" | "blue"]> = {
  open: ["offen", "blue"], partial: ["teilbezahlt", "amber"], overdue: ["überfällig", "red"], paid: ["bezahlt", "green"],
  costs_open: ["Kosten offen", "amber"], cancelled: ["storniert", "slate"],
};
const OFFER_STATUS: Record<string, [string, "slate" | "green" | "amber" | "red" | "blue"]> = {
  sent: ["offen", "blue"], accepted: ["angenommen", "green"], declined: ["abgelehnt", "slate"],
};
const LEVEL = ["", "Zahlungserinnerung", "Mahnung", "Letzte Mahnung"];
const btn = "rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800";

function iban(v: string) { return v.replace(/(.{4})/g, "$1 ").trim(); }

export default function PortalPage() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [area, setArea] = useState<"customer" | "employee">("customer");

  const load = useCallback(async () => {
    try {
      const m = await publicApi<Me>("GET", "/api/portal/me");
      setMe(m);
      if (m.employee && m.customers.length === 0) setArea("employee");
      if (m.customer_id) setData(await publicApi<Overview>("GET", "/api/portal/overview"));
    } catch (e) {
      if (e instanceof HttpError && e.status === 401) { setMe(null); setData(null); } else setError("Laden fehlgeschlagen");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function act(fn: () => Promise<unknown>, success?: string) {
    setError(null); setInfo(null);
    try { await fn(); if (success) setInfo(success); await load(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); }
  }

  if (me === undefined) return <PublicShell title="Kundenportal"><p className="text-sm text-slate-500">Lade …</p></PublicShell>;
  if (me === null) return <PublicShell title="Kundenportal"><Login onDone={load} /></PublicShell>;

  if (me.employee && area === "employee") {
    return (
      <PublicShell title="Mitarbeiter-Portal">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-2">
          <div><h1 className="text-xl font-semibold">{me.employee.name}</h1><p className="text-sm text-slate-500">Angemeldet als {me.email}</p></div>
          <div className="flex flex-wrap gap-2">
            {me.customers.length > 0 && <Button onClick={() => setArea("customer")}>Zum Kundenbereich</Button>}
            <Button onClick={() => act(() => publicApi("POST", "/api/portal/logout", { everywhere: false }))}>Abmelden</Button>
          </div>
        </div>
        {error && <div className="mb-4"><Alert>{error}</Alert></div>}
        <EmployeeArea />
      </PublicShell>
    );
  }

  return (
    <PublicShell title="Kundenportal">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">{data?.customer.name ?? "Kundenportal"}</h1>
          <p className="text-sm text-slate-500">Angemeldet als {me.email}{data ? ` · Kunden-Nr. ${data.customer.number}` : ""}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {me.employee && <Button onClick={() => setArea("employee")}>Mitarbeiterbereich</Button>}
          {me.customers.length > 1 && <Button onClick={() => act(async () => { setData(null); setMe({ ...me, customer_id: null }); })}>Kunde wechseln</Button>}
          <Button onClick={() => act(() => publicApi("POST", "/api/portal/logout", { everywhere: false }))}>Abmelden</Button>
        </div>
      </div>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {info && <div className="mb-4"><Alert kind="success">{info}</Alert></div>}

      {!me.customer_id || !data ? (
        <Card>
          <h2 className="mb-3 font-medium">Für welchen Kunden möchten Sie das Portal öffnen?</h2>
          <div className="flex flex-col gap-2">
            {me.customers.map((c) => (
              <Button key={c.id} onClick={() => act(() => publicApi("POST", "/api/portal/select", { customer_id: c.id }))}>{c.name} ({c.number})</Button>
            ))}
          </div>
        </Card>
      ) : (
        <Dashboard data={data} act={act} />
      )}
    </PublicShell>
  );
}

function Login({ onDone }: { onDone: () => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [remember, setRemember] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [devCode, setDevCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function requestCode(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const r = await publicApi<{ message: string; dev_code?: string }>("POST", "/api/portal/login/request", { email });
      setMsg(r.message); setDevCode(r.dev_code ?? null); setStep("code");
    } catch (err) { setError(err instanceof HttpError ? err.message : "Anfrage fehlgeschlagen"); } finally { setBusy(false); }
  }
  async function verify(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { await publicApi("POST", "/api/portal/login/verify", { email, code: code.trim(), remember }); await onDone(); }
    catch (err) { setError(err instanceof HttpError ? err.message : "Anmeldung fehlgeschlagen"); } finally { setBusy(false); }
  }

  return (
    <Card className="mx-auto max-w-md">
      <h1 className="mb-1 text-lg font-semibold">Anmeldung zum Kundenportal</h1>
      <p className="mb-4 text-sm text-slate-500">Rechnungen, Angebote, Verträge und Zahlungsinformationen – ohne Passwort, mit einem Code per E-Mail.</p>
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}
      {step === "email" ? (
        <form onSubmit={requestCode} className="space-y-3">
          <Field label="E-Mail-Adresse"><Input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Button type="submit" variant="primary" disabled={busy}>Code anfordern</Button>
        </form>
      ) : (
        <form onSubmit={verify} className="space-y-3">
          {msg && <Alert kind="info">{msg}</Alert>}
          {devCode && <Alert kind="warning">Entwicklungsmodus (kein Postfach eingerichtet): Code {devCode}</Alert>}
          <Field label="6-stelliger Code"><Input required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Auf diesem Gerät 30 Tage angemeldet bleiben</label>
          <div className="flex gap-2"><Button type="submit" variant="primary" disabled={busy}>Anmelden</Button><Button onClick={() => { setStep("email"); setCode(""); }}>Andere Adresse</Button></div>
        </form>
      )}
    </Card>
  );
}

function Dashboard({ data, act }: { data: Overview; act: (fn: () => Promise<unknown>, success?: string) => Promise<void> }) {
  const [payment, setPayment] = useState<Payment | null>(null);
  const [decision, setDecision] = useState<{ id: string; number: string; accept: boolean; name: string; note: string; confirm: boolean } | null>(null);
  const [terminate, setTerminate] = useState<{ id: string; number: string; name: string; date: string; reason: string } | null>(null);
  const gross = data.customer.type === "b2c";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Card><p className="text-xs text-slate-500">Offener Betrag</p><p className="text-2xl font-semibold tabular">{formatEuro(data.total_open)}</p></Card>
        <Card><p className="text-xs text-slate-500">Offene Angebote</p><p className="text-2xl font-semibold">{data.offers.filter((o) => o.status === "sent" && !o.expired).length}</p></Card>
        <Card><p className="text-xs text-slate-500">Aktive Verträge</p><p className="text-2xl font-semibold">{data.contracts.filter((c) => c.status === "active").length}</p></Card>
      </div>

      {payment && (
        <Card>
          <div className="flex flex-wrap items-center gap-6">
            {payment.girocode && <img src={payment.girocode} alt="GiroCode zum Scannen mit der Banking-App" className="h-36 w-36 rounded bg-white p-2" />}
            <dl className="grid grid-cols-[8rem_1fr] gap-1 text-sm">
              <dt className="text-slate-500">Betrag</dt><dd className="font-semibold tabular">{formatEuro(payment.amount)}</dd>
              <dt className="text-slate-500">Empfänger</dt><dd>{payment.recipient}</dd>
              <dt className="text-slate-500">IBAN</dt><dd className="font-mono">{iban(payment.iban)}</dd>
              {payment.bic && <><dt className="text-slate-500">BIC</dt><dd className="font-mono">{payment.bic}</dd></>}
              <dt className="text-slate-500">Verwendungszweck</dt><dd className="font-mono">{payment.reference}</dd>
            </dl>
          </div>
          <p className="mt-3 text-xs text-slate-500">QR-Code mit der Banking-App scannen (GiroCode) – alle Daten werden automatisch übernommen.</p>
          <div className="mt-2"><Button onClick={() => setPayment(null)}>Schließen</Button></div>
        </Card>
      )}

      <section>
        <h2 className="mb-2 font-medium">Rechnungen</h2>
        <Card>
          {data.invoices.length === 0 ? <p className="text-sm text-slate-500">Noch keine Rechnungen.</p> : (
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {data.invoices.map((i) => {
                const [label, tone] = INV_STATUS[i.status] ?? [i.status, "slate"];
                return (
                  <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span><span className="font-medium">{i.kind === "cancellation" ? "Storno " : ""}{i.number}</span> <span className="text-slate-500">vom {formatDate(i.issue_date)}</span></span>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="tabular">{formatEuro(i.total_gross)}</span>
                      <Badge tone={tone}>{label}</Badge>
                      <a className={btn} href={`/api/portal/invoices/${i.id}/document/pdf`} target="_blank" rel="noopener">PDF</a>
                      {data.customer.type === "b2b" && <a className={btn} href={`/api/portal/invoices/${i.id}/document/xml`}>E-Rechnung (XML)</a>}
                      {["open", "partial", "overdue", "costs_open"].includes(i.status) && (
                        <button className={btn} onClick={() => act(async () => setPayment(await publicApi<Payment>("GET", `/api/portal/invoices/${i.id}/payment`)))}>Jetzt bezahlen</button>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </section>

      {data.notices.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Zahlungserinnerungen &amp; Mahnungen</h2>
          <Card>
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {data.notices.map((n) => (
                <li key={n.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span>{LEVEL[n.level]} {n.number} zu {n.invoice_number} vom {formatDate(n.notice_date)} · Frist {formatDate(n.deadline)}</span>
                  <span className="flex shrink-0 items-center gap-2"><span className="tabular">{formatEuro(n.total)}</span><a className={btn} href={`/api/portal/dunning/${n.id}/pdf`} target="_blank" rel="noopener">PDF</a></span>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      )}

      <section>
        <h2 className="mb-2 font-medium">Angebote</h2>
        <Card>
          {data.offers.length === 0 ? <p className="text-sm text-slate-500">Keine Angebote.</p> : (
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {data.offers.map((o) => {
                const [label, tone] = o.expired ? ["abgelaufen", "slate" as const] : OFFER_STATUS[o.status] ?? [o.status, "slate" as const];
                return (
                  <li key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span><span className="font-medium">{o.number}</span> {o.title} <span className="text-slate-500">· gültig bis {formatDate(o.valid_until)}</span></span>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="tabular">{formatEuro(gross ? o.total_gross : o.total_net)}{!gross && <span className="text-xs text-slate-500"> netto</span>}</span>
                      <Badge tone={tone}>{label}</Badge>
                      <a className={btn} href={`/api/portal/offers/${o.id}/pdf`} target="_blank" rel="noopener">PDF</a>
                      {o.status === "sent" && !o.expired && (
                        <>
                          <button className={btn} onClick={() => setDecision({ id: o.id, number: o.number, accept: false, name: "", note: "", confirm: false })}>Ablehnen</button>
                          <button className={`${btn} border-brand-600 bg-brand-600 text-white hover:bg-brand-700`} onClick={() => setDecision({ id: o.id, number: o.number, accept: true, name: "", note: "", confirm: false })}>Annehmen</button>
                        </>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          {decision && (
            <div className="mt-4 space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
              <h3 className="font-medium">Angebot {decision.number} {decision.accept ? "annehmen" : "ablehnen"}</h3>
              <Field label="Ihr Name"><Input required value={decision.name} onChange={(e) => setDecision({ ...decision, name: e.target.value })} /></Field>
              <Field label={decision.accept ? "Anmerkung (optional)" : "Grund (optional)"}><Textarea rows={2} value={decision.note} onChange={(e) => setDecision({ ...decision, note: e.target.value })} /></Field>
              {decision.accept && (
                <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={decision.confirm} onChange={(e) => setDecision({ ...decision, confirm: e.target.checked })} />
                  Ich nehme das Angebot verbindlich an und bin berechtigt, diese Erklärung {data.customer.type === "b2b" ? "für das Unternehmen" : ""} abzugeben.</label>
              )}
              <div className="flex gap-2">
                <Button variant="primary" disabled={decision.name.trim().length < 2 || (decision.accept && !decision.confirm)}
                  onClick={() => act(async () => { await publicApi("POST", `/api/portal/offers/${decision.id}/decision`, { accept: decision.accept, name: decision.name, note: decision.note || null, confirm: decision.confirm }); setDecision(null); },
                    decision.accept ? "Vielen Dank – das Angebot ist angenommen. Wir melden uns in Kürze." : "Ihre Ablehnung wurde gespeichert.")}>
                  {decision.accept ? "Verbindlich annehmen" : "Ablehnen"}
                </Button>
                <Button onClick={() => setDecision(null)}>Abbrechen</Button>
              </div>
            </div>
          )}
        </Card>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Verträge</h2>
        <Card>
          {data.contracts.length === 0 ? <p className="text-sm text-slate-500">Keine Verträge.</p> : (
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {data.contracts.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span><span className="font-medium">{c.number}</span> {c.title} <span className="text-slate-500">· seit {formatDate(c.start_date)}{c.end_date ? ` · endet ${formatDate(c.end_date)}` : ""}</span></span>
                  {c.status === "active" && !c.notice_received && (
                    <button className={`${btn} border-red-300 text-red-700`} onClick={() => setTerminate({ id: c.id, number: c.number, name: "", date: "", reason: "" })}>Vertrag kündigen</button>
                  )}
                  {c.notice_received && <Badge tone="amber">gekündigt</Badge>}
                </li>
              ))}
            </ul>
          )}
          {terminate && (
            <div className="mt-4 space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
              <h3 className="font-medium">Vertrag {terminate.number} kündigen</h3>
              <Field label="Ihr Name"><Input required value={terminate.name} onChange={(e) => setTerminate({ ...terminate, name: e.target.value })} /></Field>
              <Field label="Gewünschter Zeitpunkt" hint="leer = zum nächstmöglichen Termin"><Input type="date" value={terminate.date} onChange={(e) => setTerminate({ ...terminate, date: e.target.value })} /></Field>
              <Field label="Grund (optional)"><Textarea rows={2} value={terminate.reason} onChange={(e) => setTerminate({ ...terminate, reason: e.target.value })} /></Field>
              <div className="flex gap-2">
                <Button variant="danger" disabled={terminate.name.trim().length < 2}
                  onClick={() => act(async () => { await publicApi("POST", `/api/portal/contracts/${terminate.id}/terminate`, { name: terminate.name, requested_date: terminate.date || null, reason: terminate.reason || null }); setTerminate(null); },
                    "Ihre Kündigung ist eingegangen. Die Eingangsbestätigung erhalten Sie per E-Mail.")}>Jetzt kündigen</Button>
                <Button onClick={() => setTerminate(null)}>Abbrechen</Button>
              </div>
            </div>
          )}
          {data.terminations.length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-800">
              {data.terminations.map((t) => (
                <li key={t.number}>{t.kind === "termination" ? "Kündigung" : "Widerruf"} {t.number}{t.contract_reference ? ` (${t.contract_reference})` : ""} eingegangen am {new Date(t.received_at).toLocaleString("de-DE")} · {t.status === "received" ? "in Bearbeitung" : t.status === "processed" ? `bestätigt${t.effective_date ? `, wirksam zum ${formatDate(t.effective_date)}` : ""}` : "abgelehnt"}</li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Ihre Daten</h2>
        <Card>
          <p className="text-sm">{data.customer.name}<br />{data.customer.street}<br />{data.customer.postal_code} {data.customer.city}<br />{data.customer.email}</p>
          <p className="mt-2 text-xs text-slate-500">Änderungen bitte per E-Mail an {data.company.email ?? data.company.name} mitteilen.</p>
        </Card>
      </section>
    </div>
  );
}
