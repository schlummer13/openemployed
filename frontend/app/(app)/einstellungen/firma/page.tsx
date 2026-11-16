"use client";

import { useEffect, useState, type FormEvent } from "react";
import { LogoUpload } from "@/components/LogoUpload";
import { Alert, Button, Card, Field, Input, PageHeader, Select } from "@/components/ui";
import { api, formatDate, HttpError, todayIso } from "@/lib/http";
import type { CompanyProfile } from "@/lib/types";

type Form = {
  valid_from: string;
  legal_name: string;
  legal_form: CompanyProfile["legal_form"];
  street: string;
  postal_code: string;
  city: string;
  register_court: string;
  register_number: string;
  managing_directors: string;
  tax_number: string;
  vat_id: string;
  iban: string;
  bic: string;
  bank_name: string;
  email: string;
  phone: string;
  website: string;
};

const EMPTY: Form = {
  valid_from: todayIso(),
  legal_name: "",
  legal_form: "UG (haftungsbeschränkt)",
  street: "",
  postal_code: "",
  city: "Düsseldorf",
  register_court: "Amtsgericht Düsseldorf",
  register_number: "",
  managing_directors: "",
  tax_number: "",
  vat_id: "",
  iban: "",
  bic: "",
  bank_name: "",
  email: "",
  phone: "",
  website: "",
};

function fromProfile(p: CompanyProfile): Form {
  return {
    valid_from: todayIso(),
    legal_name: p.legal_name,
    legal_form: p.legal_form,
    street: p.street,
    postal_code: p.postal_code,
    city: p.city,
    register_court: p.register_court ?? "",
    register_number: p.register_number ?? "",
    managing_directors: p.managing_directors.join("\n"),
    tax_number: p.tax_number ?? "",
    vat_id: p.vat_id ?? "",
    iban: p.iban ?? "",
    bic: p.bic ?? "",
    bank_name: p.bank_name ?? "",
    email: p.email ?? "",
    phone: p.phone ?? "",
    website: p.website ?? "",
  };
}

export default function CompanyPage() {
  const [current, setCurrent] = useState<CompanyProfile | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<CompanyProfile | null>("GET", "/api/company-profile")
      .then((p) => {
        setCurrent(p);
        if (p) setForm(fromProfile(p));
      })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, []);

  const set = (k: keyof Form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const p = await api<CompanyProfile>("POST", "/api/company-profile", {
        ...form,
        managing_directors: form.managing_directors.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      setCurrent(p);
      setSaved(true);
    } catch (err) {
      setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Firmendaten"
        description="Pflichtangaben für Rechnungen und Geschäftsbriefe (§ 14 UStG, § 35a GmbHG). Jede Änderung wird als neue Version mit Gültigkeitsdatum gespeichert – bereits ausgestellte Rechnungen behalten ihre Angaben."
      />
      {current && (
        <div className="mb-4">
          {current.missing_for_invoices.length > 0 ? (
            <Alert kind="warning">Für Rechnungen fehlt noch: {current.missing_for_invoices.join(", ")}.</Alert>
          ) : (
            <Alert kind="success">Alle Pflichtangaben für Rechnungen sind vorhanden (gültig ab {formatDate(current.valid_from)}).</Alert>
          )}
        </div>
      )}
      <LogoUpload />
      <Card>
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <Field label="Gültig ab" hint="Neue Version gilt ab diesem Datum">
            <Input type="date" required value={form.valid_from} onChange={set("valid_from")} />
          </Field>
          <Field label="Rechtsform">
            <Select value={form.legal_form} onChange={set("legal_form")}>
              <option>UG (haftungsbeschränkt)</option>
              <option>GmbH</option>
            </Select>
          </Field>
          <Field label="Firmenname inkl. Rechtsform" className="md:col-span-2" hint="z. B. „Beispiel Software UG (haftungsbeschränkt)“">
            <Input required value={form.legal_name} onChange={set("legal_name")} />
          </Field>
          <Field label="Straße und Hausnummer">
            <Input required value={form.street} onChange={set("street")} />
          </Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label="PLZ">
              <Input required inputMode="numeric" pattern="[0-9]{5}" value={form.postal_code} onChange={set("postal_code")} />
            </Field>
            <Field label="Ort" className="col-span-2">
              <Input required value={form.city} onChange={set("city")} />
            </Field>
          </div>
          <Field label="Registergericht">
            <Input value={form.register_court} onChange={set("register_court")} />
          </Field>
          <Field label="Handelsregisternummer" hint="z. B. HRB 12345">
            <Input value={form.register_number} onChange={set("register_number")} />
          </Field>
          <Field label="Geschäftsführer" hint="Ein Name pro Zeile – alle vier Gründer" className="md:col-span-2">
            <textarea
              rows={4}
              required
              value={form.managing_directors}
              onChange={set("managing_directors")}
              className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
            />
          </Field>
          <Field label="Steuernummer">
            <Input value={form.tax_number} onChange={set("tax_number")} />
          </Field>
          <Field label="USt-IdNr" hint="Format DE123456789">
            <Input value={form.vat_id} onChange={set("vat_id")} />
          </Field>
          <Field label="IBAN">
            <Input value={form.iban} onChange={set("iban")} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="BIC">
              <Input value={form.bic} onChange={set("bic")} />
            </Field>
            <Field label="Bank">
              <Input value={form.bank_name} onChange={set("bank_name")} />
            </Field>
          </div>
          <Field label="E-Mail">
            <Input type="email" value={form.email} onChange={set("email")} />
          </Field>
          <Field label="Telefon">
            <Input value={form.phone} onChange={set("phone")} />
          </Field>
          <Field label="Website" className="md:col-span-2">
            <Input value={form.website} onChange={set("website")} />
          </Field>
          <div className="space-y-3 md:col-span-2">
            {error && <Alert>{error}</Alert>}
            {saved && <Alert kind="success">Neue Version gespeichert.</Alert>}
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Speichere …" : "Als neue Version speichern"}
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}
