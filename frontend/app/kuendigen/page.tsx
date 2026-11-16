"use client";

import { useState, type FormEvent } from "react";
import { PublicShell } from "@/components/PublicShell";
import { Alert, Button, Card, Field, Input, Select, Textarea } from "@/components/ui";
import { HttpError, publicApi } from "@/lib/http";

interface Receipt { number: string; kind: string; received_at: string; confirmation_sent: boolean }

/** Kündigungsschaltfläche nach § 312k BGB – ohne Anmeldung erreichbar. */
export default function TerminationPage() {
  const [form, setForm] = useState({ kind: "termination", termination_type: "ordinary", name: "", email: "", customer_number: "", contract_reference: "", requested_date: "", reason: "" });
  const [step, setStep] = useState<"form" | "confirm" | "done">("form");
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch });
  const isWithdrawal = form.kind === "withdrawal";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (step === "form") { setStep("confirm"); return; }
    setBusy(true); setError(null);
    try {
      const r = await publicApi<Receipt>("POST", "/api/public/terminations", { ...form, requested_date: form.requested_date || null });
      setReceipt(r); setStep("done");
    } catch (err) { setError(err instanceof HttpError ? err.message : "Senden fehlgeschlagen – bitte kündigen Sie alternativ per E-Mail."); setStep("form"); }
    finally { setBusy(false); }
  }

  return (
    <PublicShell title={isWithdrawal ? "Vertrag widerrufen" : "Vertrag kündigen"}>
      <Card className="mx-auto max-w-xl">
        {step === "done" && receipt ? (
          <div className="space-y-3">
            <h1 className="text-lg font-semibold">{receipt.kind === "termination" ? "Kündigung" : "Widerruf"} eingegangen</h1>
            <p className="text-sm">Eingangsnummer <strong>{receipt.number}</strong>, eingegangen am {new Date(receipt.received_at).toLocaleString("de-DE")} Uhr.</p>
            <p className="text-sm">{receipt.confirmation_sent ? "Die Eingangsbestätigung haben wir Ihnen per E-Mail gesendet." : "Bitte speichern oder drucken Sie diese Seite als Nachweis; die E-Mail-Bestätigung folgt."}</p>
            <Button onClick={() => window.print()}>Seite drucken</Button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <h1 className="text-lg font-semibold">{isWithdrawal ? "Vertrag widerrufen" : "Vertrag kündigen"}</h1>
            {error && <Alert>{error}</Alert>}
            <Field label="Art der Erklärung">
              <Select value={form.kind} onChange={(e) => set({ kind: e.target.value })} disabled={step === "confirm"}>
                <option value="termination">Kündigung</option>
                <option value="withdrawal">Widerruf (innerhalb von 14 Tagen nach Vertragsschluss)</option>
              </Select>
            </Field>
            {!isWithdrawal && (
              <Field label="Kündigungsart">
                <Select value={form.termination_type} onChange={(e) => set({ termination_type: e.target.value })} disabled={step === "confirm"}>
                  <option value="ordinary">ordentliche Kündigung</option>
                  <option value="extraordinary">außerordentliche Kündigung (bitte Grund angeben)</option>
                </Select>
              </Field>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Name"><Input required minLength={2} value={form.name} onChange={(e) => set({ name: e.target.value })} disabled={step === "confirm"} /></Field>
              <Field label="E-Mail für die Bestätigung"><Input type="email" required value={form.email} onChange={(e) => set({ email: e.target.value })} disabled={step === "confirm"} /></Field>
              <Field label="Kundennummer (falls bekannt)"><Input value={form.customer_number} onChange={(e) => set({ customer_number: e.target.value })} disabled={step === "confirm"} /></Field>
              <Field label="Vertrag / Vertragsnummer"><Input value={form.contract_reference} onChange={(e) => set({ contract_reference: e.target.value })} disabled={step === "confirm"} /></Field>
            </div>
            {!isWithdrawal && (
              <Field label="Gewünschter Zeitpunkt" hint="leer = zum nächstmöglichen Zeitpunkt"><Input type="date" value={form.requested_date} onChange={(e) => set({ requested_date: e.target.value })} disabled={step === "confirm"} /></Field>
            )}
            <Field label={form.termination_type === "extraordinary" && !isWithdrawal ? "Grund (erforderlich)" : "Anmerkung (optional)"}>
              <Textarea rows={3} required={form.termination_type === "extraordinary" && !isWithdrawal} value={form.reason} onChange={(e) => set({ reason: e.target.value })} disabled={step === "confirm"} />
            </Field>
            {step === "confirm" ? (
              <div className="space-y-2">
                <Alert kind="warning">Bitte prüfen Sie Ihre Angaben. Mit Klick auf die Schaltfläche geben Sie Ihre Erklärung verbindlich ab.</Alert>
                <div className="flex gap-2">
                  <Button type="submit" variant="danger" disabled={busy}>{isWithdrawal ? "Jetzt widerrufen" : "Jetzt kündigen"}</Button>
                  <Button onClick={() => setStep("form")}>Angaben ändern</Button>
                </div>
              </div>
            ) : (
              <Button type="submit" variant="primary">{isWithdrawal ? "Weiter zum Widerruf" : "Weiter zur Kündigung"}</Button>
            )}
          </form>
        )}
      </Card>
    </PublicShell>
  );
}
