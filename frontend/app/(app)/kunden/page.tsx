"use client";

import { PortalAccess } from "@/components/PortalAccess";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Table, Textarea } from "@/components/ui";
import { api, HttpError } from "@/lib/http";
import type { Customer, CustomerInput, CustomerType } from "@/lib/types";

const EMPTY: CustomerInput = {
  customer_type: "b2b",
  company_name: "",
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  street: "",
  postal_code: "",
  city: "",
  vat_id: "",
  buyer_reference: "",
  payment_terms_days: 14,
  notes: "",
  country_code: "DE",
  currency: "EUR",
  einvoice_format: "facturx",
  peppol_id: "",
};
const COUNTRIES: [string, string][] = [["DE", "Deutschland"], ["AT", "Österreich"], ["CH", "Schweiz"], ["NL", "Niederlande"], ["BE", "Belgien"],
  ["LU", "Luxemburg"], ["FR", "Frankreich"], ["IT", "Italien"], ["ES", "Spanien"], ["PL", "Polen"], ["CZ", "Tschechien"], ["DK", "Dänemark"],
  ["SE", "Schweden"], ["IE", "Irland"], ["PT", "Portugal"], ["FI", "Finnland"], ["GB", "Vereinigtes Königreich"], ["US", "USA"], ["CA", "Kanada"],
  ["NO", "Norwegen"], ["AU", "Australien"], ["JP", "Japan"]];
const CURRENCIES = ["EUR", "USD", "GBP", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "JPY", "CAD", "AUD"];

function toInput(c: Customer): CustomerInput {
  const { id: _i, number: _n, display_name: _d, archived: _a, ...rest } = c;
  return { ...rest, country_code: c.country_code ?? "DE", currency: c.currency ?? "EUR", einvoice_format: c.einvoice_format ?? "facturx", peppol_id: c.peppol_id ?? "" };
}

export default function CustomersPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [editing, setEditing] = useState<{ id: string | null; data: CustomerInput } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [welcome, setWelcome] = useState(true);
  const { toast, confirm } = useFeedback();
  const setInfo = (m: string | null) => { if (m) toast(m); };
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    api<Customer[]>("GET", `/api/customers?include_archived=${showArchived}`)
      .then((c) => { setCustomers(c); setLoaded(true); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, [showArchived]);
  useEffect(load, [load]);

  const set = <K extends keyof CustomerInput>(k: K, v: CustomerInput[K]) =>
    setEditing((e) => (e ? { ...e, data: { ...e.data, [k]: v } } : e));

  async function save(ev: FormEvent) {
    ev.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const body = { ...editing.data, payment_terms_days: Number(editing.data.payment_terms_days), peppol_id: editing.data.peppol_id || null };
      setInfo(null);
      if (editing.id) { await api("PUT", `/api/customers/${editing.id}`, body); setInfo("Kunde gespeichert."); }
      else {
        const created = await api<{ welcome?: { mail_sent: boolean; reason?: string } }>("POST", `/api/customers?welcome=${welcome}`, body);
        if (!created.welcome) setInfo("Kunde angelegt.");
        if (created.welcome) setInfo(created.welcome.mail_sent ? "Kunde angelegt – Willkommensmail mit Portalzugang wurde versendet." : `Kunde angelegt, Portalzugang freigeschaltet. Willkommensmail nicht versendet: ${created.welcome.reason ?? "unbekannt"}.`);
      }
      setEditing(null);
      load();
    } catch (err) {
      setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function toggleArchive(c: Customer) {
    setError(null);
    if (!c.archived && !(await confirm({ title: `${c.display_name ?? "Kunde"} archivieren?`, confirmLabel: "Archivieren",
      message: "Der Kunde wird ausgeblendet, aber nie gelöscht (Aufbewahrungspflicht). Wiederherstellen ist jederzeit möglich." }))) return;
    try {
      await api("POST", `/api/customers/${c.id}/${c.archived ? "restore" : "archive"}`);
      load();
    } catch (err) {
      setError(err instanceof HttpError ? err.message : "Aktion fehlgeschlagen");
    }
  }

  const d = editing?.data;
  if (!loaded && !error) return <PageSkeleton stats={0} />;
  return (
    <>
      <PageHeader
        title="Kunden"
        description="Firmen (B2B) und Privatkunden (B2C) im In- und Ausland. Kunden werden archiviert, nie gelöscht."
        actions={
          <>
            <Button onClick={() => setShowArchived((s) => !s)}>{showArchived ? "Archivierte ausblenden" : "Archivierte zeigen"}</Button>
            <Button variant="primary" onClick={() => setEditing({ id: null, data: { ...EMPTY } })}>
              + Kunde
            </Button>
          </>
        }
      />
      {error && !editing && <div className="mb-4"><Alert>{error}</Alert></div>}

      <Drawer open={!!editing} onClose={() => { setEditing(null); setError(null); }} size="lg"
        title={editing?.id ? "Kunde bearbeiten" : "Neuer Kunde"}
        footer={<>
          {!editing?.id && <label className="mr-auto flex items-center gap-2 text-sm"><input type="checkbox" checked={welcome} onChange={(e) => setWelcome(e.target.checked)} /> Willkommensmail mit Portalzugang senden</label>}
          <Button onClick={() => { setEditing(null); setError(null); }}>Abbrechen</Button>
          <Button type="submit" form="customer-form" variant="primary" disabled={busy}>{busy ? "Speichere …" : "Speichern"}</Button>
        </>}>
      {editing && d && (
        <>
          <form id="customer-form" onSubmit={save} className="grid gap-4 md:grid-cols-2">
            <Field label="Kundentyp" className="md:col-span-2">
              <div role="radiogroup" className="inline-flex rounded-lg border border-slate-300 p-0.5 dark:border-slate-700">
                {(["b2b", "b2c"] as CustomerType[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    role="radio"
                    aria-checked={d.customer_type === t}
                    onClick={() => set("customer_type", t)}
                    className={`rounded-md px-3 py-1 text-sm ${d.customer_type === t ? "bg-brand-600 text-white" : ""}`}
                  >
                    {t === "b2b" ? "Firma (B2B)" : "Privatperson (B2C)"}
                  </button>
                ))}
              </div>
            </Field>
            {d.customer_type === "b2b" ? (
              <Field label="Firmenname" className="md:col-span-2">
                <Input required value={d.company_name ?? ""} onChange={(e) => set("company_name", e.target.value)} />
              </Field>
            ) : (
              <>
                <Field label="Vorname">
                  <Input value={d.first_name ?? ""} onChange={(e) => set("first_name", e.target.value)} />
                </Field>
                <Field label="Nachname">
                  <Input required value={d.last_name ?? ""} onChange={(e) => set("last_name", e.target.value)} />
                </Field>
              </>
            )}
            <Field label="E-Mail für Rechnungen">
              <Input type="email" required value={d.email} onChange={(e) => set("email", e.target.value)} />
            </Field>
            <Field label="Telefon">
              <Input value={d.phone ?? ""} onChange={(e) => set("phone", e.target.value)} />
            </Field>
            <Field label="Straße und Hausnummer">
              <Input required value={d.street} onChange={(e) => set("street", e.target.value)} />
            </Field>
            <div className="grid grid-cols-3 gap-2">
              <Field label="PLZ">
                <Input required inputMode={d.country_code === "DE" ? "numeric" : "text"} pattern={d.country_code === "DE" ? "[0-9]{5}" : undefined} value={d.postal_code} onChange={(e) => set("postal_code", e.target.value)} />
              </Field>
              <Field label="Ort" className="col-span-2">
                <Input required value={d.city} onChange={(e) => set("city", e.target.value)} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Land">
                <Select value={d.country_code ?? "DE"} onChange={(e) => set("country_code", e.target.value)}>{COUNTRIES.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</Select>
              </Field>
              <Field label="Rechnungswährung" hint={d.currency !== "EUR" ? "Buchung in EUR zum EZB-Kurs" : undefined}>
                <Select value={d.currency ?? "EUR"} onChange={(e) => set("currency", e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select>
              </Field>
            </div>
            {d.customer_type === "b2b" && (
              <>
                <Field label="USt-IdNr (optional)">
                  <Input value={d.vat_id ?? ""} onChange={(e) => set("vat_id", e.target.value)} />
                </Field>
                <Field label="E-Rechnungsformat" hint={d.einvoice_format === "xrechnung" ? "Behörden und öffentliche Auftraggeber (Leitweg-ID Pflicht)" : "ZUGFeRD-PDF mit eingebetteter XML"}>
                  <Select value={d.einvoice_format ?? "facturx"} onChange={(e) => set("einvoice_format", e.target.value as "facturx" | "xrechnung")}><option value="facturx">ZUGFeRD / Factur-X</option><option value="xrechnung">XRechnung 3.0</option></Select>
                </Field>
                <Field label={d.einvoice_format === "xrechnung" ? "Leitweg-ID" : "Bestell-/Referenznummer des Kunden"} hint={d.einvoice_format === "xrechnung" ? "z. B. 05315000-SV001-41 – vom Auftraggeber" : "Erscheint auf jeder Rechnung"}>
                  <Input required={d.einvoice_format === "xrechnung"} value={d.buyer_reference ?? ""} onChange={(e) => set("buyer_reference", e.target.value)} />
                </Field>
                <Field label="Peppol-ID (optional)" hint="für den Versand über Peppol, z. B. 0204:991-12345-67 oder 9930:DE123456789">
                  <Input value={d.peppol_id ?? ""} onChange={(e) => set("peppol_id", e.target.value)} />
                </Field>
              </>
            )}
            <Field label="Zahlungsziel (Tage)">
              <Input type="number" min={0} max={120} value={d.payment_terms_days} onChange={(e) => set("payment_terms_days", Number(e.target.value))} />
            </Field>
            <Field label="Notizen" className="md:col-span-2">
              <Textarea value={d.notes ?? ""} onChange={(e) => set("notes", e.target.value)} />
            </Field>
            {error && <div className="md:col-span-2"><Alert>{error}</Alert></div>}
          </form>
          {editing.id && <div className="mt-6"><PortalAccess customerId={editing.id} defaultEmail={editing.data.email ?? ""} /></div>}
        </>
      )}
      </Drawer>

      <Card>
        {customers.length === 0 ? (
          <Empty>Noch keine Kunden. Lege den ersten mit „+ Kunde“ an.</Empty>
        ) : (
          <Table
            head={
              <tr>
                <th className="pb-2 font-medium">Nr.</th>
                <th className="pb-2 font-medium">Name</th>
                <th className="pb-2 font-medium">Typ</th>
                <th className="pb-2 font-medium">Ort</th>
                <th className="pb-2 font-medium">E-Mail</th>
                <th className="pb-2" />
              </tr>
            }
          >
            {customers.map((c) => (
              <tr key={c.id} className={c.archived ? "opacity-60" : ""}>
                <td className="py-2 tabular text-slate-500">{c.number}</td>
                <td className="py-2 font-medium">{c.display_name}</td>
                <td className="py-2"><Badge tone={c.customer_type === "b2b" ? "blue" : "slate"}>{c.customer_type === "b2b" ? "Firma" : "Privat"}</Badge></td>
                <td className="py-2">{c.postal_code} {c.city}</td>
                <td className="py-2 text-slate-500">{c.email}</td>
                <td className="py-2 text-right whitespace-nowrap">
                  <Button className="mr-2" onClick={() => setEditing({ id: c.id, data: toInput(c) })}>Bearbeiten</Button>
                  <Button onClick={() => toggleArchive(c)}>{c.archived ? "Wiederherstellen" : "Archivieren"}</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
