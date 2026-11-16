"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "@/components/Feedback";
import { Icon } from "@/components/Icon";
import { Alert, Button, Card, Field, Input, PageHeader, PageSkeleton, Select } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

interface F { key: string; kind: string; options: string[]; value: unknown }
interface Provider { key: string; label: string; auth: string[]; hint: string; smtp_host: string }
interface SetupItem { key: string; done: boolean }

const STEPS = ["Firma", "Steuern", "E-Mail", "Anmeldung & Team", "Bank & Daten", "Fertig"] as const;

export default function SetupWizard() {
  const [step, setStep] = useState(0);
  const [fields, setFields] = useState<Record<string, F> | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [setup, setSetup] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const { toast } = useFeedback();
  const load = useCallback(() => {
    void api<{ fields: F[] }>("GET", "/api/admin/settings").then((r) => { setFields(Object.fromEntries(r.fields.map((f) => [f.key, f]))); setDraft({}); });
    void api<Provider[]>("GET", "/api/mail/providers").then(setProviders).catch(() => undefined);
    void api<{ items: SetupItem[] }>("GET", "/api/setup").then((r) => setSetup(Object.fromEntries(r.items.map((i) => [i.key, i.done]))));
  }, []);
  useEffect(load, [load]);
  if (!fields) return <PageSkeleton stats={0} />;
  const v = (k: string) => (k in draft ? draft[k] : fields[k]?.kind === "secret" ? "" : fields[k]?.value ?? "");
  const set = (k: string, val: unknown) => setDraft({ ...draft, [k]: val });
  const save = async (next = true) => {
    setBusy(true);
    try {
      if (Object.keys(draft).length) await api("PUT", "/api/admin/settings", { changes: draft });
      load();
      if (next) setStep((s) => Math.min(s + 1, STEPS.length - 1));
    } catch (e) { toast(e instanceof HttpError ? e.message : "Speichern fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const provider = providers.find((p) => p.key === v("mail_provider"));

  return (
    <>
      <PageHeader title="Einrichtung" eyebrow="Erste Schritte" description="In sechs Schritten startklar – alles lässt sich später unter System & Integrationen ändern." />
      <ol className="mb-6 flex flex-wrap gap-2">
        {STEPS.map((s, i) => (
          <li key={s}><button type="button" onClick={() => setStep(i)} className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium ${i === step ? "bg-brand-600 text-white" : i < step ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-slate-100 text-slate-600 dark:bg-white/5 dark:text-slate-300"}`}>
            <span className="grid size-5 place-items-center rounded-full bg-white/20">{i < step ? <Icon name="check" className="size-3" /> : i + 1}</span>{s}
          </button></li>
        ))}
      </ol>
      <Card className="max-w-3xl">
        {step === 0 && (
          <div className="grid gap-4">
            <h2 className="text-lg font-semibold">Firmendaten</h2>
            <p className="text-sm text-slate-600 dark:text-slate-300">Name, Anschrift, Register, Steuernummer und Bankverbindung erscheinen auf Rechnungen und in den Meldungen ans Finanzamt.</p>
            {setup.company ? <Alert kind="success">Firmenprofil ist vollständig.</Alert> : <Alert kind="info">Noch nicht vollständig.</Alert>}
            <div className="flex flex-wrap gap-2"><Link href="/einstellungen/firma" className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700">Firmenprofil öffnen</Link><Button onClick={() => setStep(1)}>Weiter</Button></div>
          </div>
        )}
        {step === 1 && (
          <div className="grid gap-4 sm:grid-cols-2">
            <h2 className="text-lg font-semibold sm:col-span-2">Steuern</h2>
            <Field label="Bundesland (Finanzamt)"><Select value={String(v("company_state"))} onChange={(e) => set("company_state", e.target.value)}>{fields.company_state!.options.map((o) => <option key={o}>{o}</option>)}</Select></Field>
            <Field label="Umsatzsteuer-Voranmeldung"><Select value={String(v("vat_filing"))} onChange={(e) => set("vat_filing", e.target.value)}><option value="monthly">monatlich</option><option value="quarterly">vierteljährlich</option></Select></Field>
            <Field label="Gewerbesteuer-Hebesatz (%)" hint="Düsseldorf 440, Köln 475, München 490"><Input type="number" value={String(v("trade_tax_multiplier"))} onChange={(e) => set("trade_tax_multiplier", Number(e.target.value))} /></Field>
            <p className="text-xs text-slate-500 sm:col-span-2">Kontenrahmen (SKR03/SKR04) wählt ihr unter Buchhaltung → DATEV; Dauerfristverlängerung und Vorauszahlungen unter Steuern.</p>
            <div className="sm:col-span-2"><Button variant="primary" disabled={busy} onClick={() => void save()}>Speichern & weiter</Button></div>
          </div>
        )}
        {step === 2 && (
          <div className="grid gap-4 sm:grid-cols-2">
            <h2 className="text-lg font-semibold sm:col-span-2">E-Mail für Rechnungen, Mahnungen und Belegeingang</h2>
            <Field label="Anbieter" className="sm:col-span-2" hint={provider?.hint}>
              <Select value={String(v("mail_provider"))} onChange={(e) => { const p = providers.find((x) => x.key === e.target.value); setDraft({ ...draft, mail_provider: e.target.value, mail_auth_mode: p?.auth[0] ?? "app_password" }); }}>
                {providers.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
              </Select>
            </Field>
            <Field label="Absender-Postfach"><Input type="email" placeholder="rechnungen@firma.de" value={String(v("mail_mailbox"))} onChange={(e) => set("mail_mailbox", e.target.value)} /></Field>
            <Field label="Absendername"><Input placeholder="Beispiel UG" value={String(v("mail_from_name"))} onChange={(e) => set("mail_from_name", e.target.value)} /></Field>
            {v("mail_auth_mode") === "oauth_client" ? <>
              <Field label="Verzeichnis-ID (Tenant)"><Input value={String(v("ms_tenant_id"))} onChange={(e) => set("ms_tenant_id", e.target.value)} /></Field>
              <Field label="Anwendungs-ID"><Input value={String(v("ms_client_id"))} onChange={(e) => set("ms_client_id", e.target.value)} /></Field>
              <Field label="Clientschlüssel" className="sm:col-span-2"><Input type="password" autoComplete="off" placeholder={fields.ms_client_secret?.value ? "gesetzt – leer = unverändert" : ""} value={String(v("ms_client_secret"))} onChange={(e) => set("ms_client_secret", e.target.value)} /></Field>
            </> : v("mail_auth_mode") === "app_password" ? <>
              <Field label="Passwort / App-Passwort"><Input type="password" autoComplete="off" placeholder={fields.mail_app_password?.value ? "gesetzt – leer = unverändert" : ""} value={String(v("mail_app_password"))} onChange={(e) => set("mail_app_password", e.target.value)} /></Field>
              {provider && !provider.smtp_host && <Field label="SMTP-Server"><Input value={String(v("smtp_host"))} onChange={(e) => set("smtp_host", e.target.value)} /></Field>}
              {provider && !provider.smtp_host && <Field label="IMAP-Server"><Input value={String(v("imap_host"))} onChange={(e) => set("imap_host", e.target.value)} /></Field>}
            </> : <p className="text-sm text-slate-500 sm:col-span-2">Google-Dienstkonto: Schlüsseldatei unter secrets/google-mail-service-account.json ablegen (docs/betrieb.md).</p>}
            <div className="flex flex-wrap gap-2 sm:col-span-2">
              <Button variant="primary" disabled={busy} onClick={() => void save(false).then(async () => { try { const r = await api<{ to: string }>("POST", "/api/mail/test"); toast(`Gespeichert – Testmail an ${r.to} gesendet.`); setStep(3); } catch (e) { toast(e instanceof HttpError ? e.message : "Testmail fehlgeschlagen", "error"); } })}>Speichern & Testmail senden</Button>
              <Button onClick={() => setStep(3)}>Überspringen</Button>
            </div>
          </div>
        )}
        {step === 3 && (
          <div className="grid gap-4">
            <h2 className="text-lg font-semibold">Anmeldung & Team</h2>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(v("auth_password_enabled"))} onChange={(e) => set("auth_password_enabled", e.target.checked)} />Anmeldung mit E-Mail und Passwort erlauben</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(v("auth_require_2fa"))} onChange={(e) => set("auth_require_2fa", e.target.checked)} />Zwei-Faktor-Code verpflichtend</label>
            <Field label="Neue Firmenkonten (Google/Microsoft) erhalten die Rolle" className="max-w-sm"><Select value={String(v("auth_default_role"))} onChange={(e) => set("auth_default_role", e.target.value)}><option value="admin">Geschäftsführung</option><option value="accounting">Buchhaltung</option><option value="employee">Mitarbeiter</option><option value="none">keine – nur eingeladene Benutzer</option></Select></Field>
            <p className="text-sm text-slate-500">Microsoft-/OIDC-Anmeldung unter <Link className="text-brand-600 hover:underline" href="/einstellungen/system">System & Integrationen</Link>; Kollegen und Steuerkanzlei einladen unter <Link className="text-brand-600 hover:underline" href="/einstellungen/benutzer">Benutzer & Rollen</Link>.</p>
            <div><Button variant="primary" disabled={busy} onClick={() => void save()}>Speichern & weiter</Button></div>
          </div>
        )}
        {step === 4 && (
          <div className="grid gap-3">
            <h2 className="text-lg font-semibold">Bank & bestehende Daten</h2>
            {[["/buchhaltung", "Kontoauszug importieren oder Onlinebanking (FinTS) verbinden", setup.bank], ["/import", "Von Lexoffice, sevDesk oder DATEV umsteigen: Eröffnungsbilanz, Kunden, Buchungen", undefined],
              ["/mitarbeiter", "Mitarbeiter und Gehälter anlegen", undefined], ["/zahlungsverkehr", "Gläubiger-ID für Lastschriften hinterlegen", undefined]].map(([href, label, done]) => (
              <Link key={String(href)} href={String(href)} className="flex items-center gap-3 rounded-lg border border-slate-200 px-4 py-3 text-sm hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/[0.03]">
                <span className={`grid size-5 place-items-center rounded-full ${done ? "bg-emerald-500 text-white" : "ring-1 ring-slate-300"}`}>{done ? <Icon name="check" className="size-3" /> : null}</span>
                <span className="flex-1">{String(label)}</span><Icon name="chevron" className="size-4 text-slate-400" />
              </Link>
            ))}
            <div><Button variant="primary" onClick={() => setStep(5)}>Weiter</Button></div>
          </div>
        )}
        {step === 5 && (
          <div className="grid gap-3 text-center">
            <span className="mx-auto grid size-12 place-items-center rounded-full bg-emerald-500 text-white"><Icon name="check" className="size-6" /></span>
            <h2 className="text-lg font-semibold">Startklar</h2>
            <p className="text-sm text-slate-500">Die Übersicht zeigt weiterhin, was für den Produktivstart noch offen ist.</p>
            <div><Link href="/" className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700">Zur Übersicht</Link></div>
          </div>
        )}
      </Card>
    </>
  );
}
