"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { MyAbsences } from "@/components/Absences";
import { FeedbackProvider } from "@/components/Feedback";
import { MyDocuments } from "@/components/Personnel";
import { TimeAccount } from "@/components/TimeAccount";
import { TimeClock } from "@/components/TimeClock";
import { Alert, Button, Card, Field, Input, Tabs } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { HttpError, publicApi } from "@/lib/http";

interface Profile {
  number: number; first_name: string; last_name: string; email: string; phone: string | null; street: string | null; postal_code: string | null;
  city: string | null; position: string | null; entry_date: string; employment_type: string; tax_class: number; child_allowances: string;
  church_tax: boolean; health_insurance: string; health_insurer: string | null; iban: string | null; account_holder: string | null; vacation_days: number | null;
}
interface Data { profile: Profile; payslips: { id: string; period: string; gross: string; net: string; payout: string }[] }

const monthLabel = (p: string) => new Date(`${p}-01T00:00:00`).toLocaleDateString("de-DE", { month: "long", year: "numeric" });

/** Self-Service für Mitarbeiter: Stempeluhr, Urlaub, Gehaltsabrechnungen und eigene Kontaktdaten/Bankverbindung. */
export function EmployeeArea() {
  const [tab, setTab] = useState<"clock" | "account" | "absence" | "docs" | "pay">("clock");
  return (
    <FeedbackProvider>
      <Tabs value={tab} onChange={setTab} items={[{ value: "clock", label: "Stempeluhr" }, { value: "account", label: "Zeitkonto" }, { value: "absence", label: "Urlaub" },
        { value: "docs", label: "Dokumente" }, { value: "pay", label: "Abrechnungen & Daten" }]} />
      {tab === "clock" && <TimeClock base="/api/portal/employee" fetcher={publicApi} compact />}
      {tab === "account" && <TimeAccount path="/api/portal/employee/time-account" fetcher={publicApi} />}
      {tab === "absence" && <MyAbsences base="/api/portal/employee" fetcher={publicApi} />}
      {tab === "docs" && <MyDocuments base="/api/portal/employee" fetcher={publicApi} />}
      {tab === "pay" && <PayArea />}
    </FeedbackProvider>
  );
}

function PayArea() {
  const [data, setData] = useState<Data | null>(null);
  const [edit, setEdit] = useState<{ phone: string; street: string; postal_code: string; city: string; iban: string; account_holder: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const load = useCallback(() => { void publicApi<Data>("GET", "/api/portal/employee").then(setData).catch(() => setError("Laden fehlgeschlagen")); }, []);
  useEffect(load, [load]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    setError(null);
    const body: Record<string, string> = { phone: edit.phone, street: edit.street, postal_code: edit.postal_code, city: edit.city, account_holder: edit.account_holder };
    if (edit.iban.trim()) body.iban = edit.iban.trim();
    try { await publicApi("PUT", "/api/portal/employee", body); setEdit(null); setInfo("Ihre Angaben wurden gespeichert."); load(); }
    catch (err) { setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen"); }
  };

  if (!data) return error ? <Alert>{error}</Alert> : <p className="text-sm text-slate-500">Lade …</p>;
  const p = data.profile;
  const years = [...new Set(data.payslips.map((s) => s.period.slice(0, 4)))];
  return (
    <div className="grid gap-6">
      {info && <Alert kind="success">{info}</Alert>}
      <Card>
        <h2 className="mb-3 font-medium">Gehaltsabrechnungen</h2>
        {data.payslips.length === 0 ? <p className="text-sm text-slate-500">Noch keine freigegebenen Abrechnungen.</p> : years.map((y) => (
          <div key={y} className="mb-4 last:mb-0">
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{y}</p>
            <ul className="divide-y divide-slate-100 dark:divide-white/5">
              {data.payslips.filter((s) => s.period.startsWith(y)).map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
                  <span className="min-w-0 flex-1 font-medium">{monthLabel(s.period)}<span className="block text-xs font-normal text-slate-500 tabular">brutto {formatEuro(s.gross)} · ausgezahlt {formatEuro(s.payout)}</span></span>
                  <a className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-300" href={`/api/portal/employee/payslips/${s.id}/pdf`} target="_blank" rel="noreferrer">PDF öffnen</a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Card>
      <Card>
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="font-medium">Meine Daten</h2>
          {!edit && <Button onClick={() => setEdit({ phone: p.phone ?? "", street: p.street ?? "", postal_code: p.postal_code ?? "", city: p.city ?? "", iban: "", account_holder: p.account_holder ?? "" })}>Ändern</Button>}
        </div>
        {edit ? (
          <form onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Straße" className="sm:col-span-2"><Input value={edit.street} onChange={(e) => setEdit({ ...edit, street: e.target.value })} /></Field>
            <Field label="PLZ"><Input value={edit.postal_code} onChange={(e) => setEdit({ ...edit, postal_code: e.target.value })} /></Field>
            <Field label="Ort"><Input value={edit.city} onChange={(e) => setEdit({ ...edit, city: e.target.value })} /></Field>
            <Field label="Telefon"><Input value={edit.phone} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field>
            <Field label="Neue IBAN" hint={`aktuell ${p.iban ?? "–"} · leer lassen = unverändert`}><Input value={edit.iban} onChange={(e) => setEdit({ ...edit, iban: e.target.value })} /></Field>
            <Field label="Kontoinhaber" className="sm:col-span-2"><Input value={edit.account_holder} onChange={(e) => setEdit({ ...edit, account_holder: e.target.value })} /></Field>
            {error && <div className="sm:col-span-2"><Alert>{error}</Alert></div>}
            <div className="flex flex-wrap justify-end gap-2 sm:col-span-2"><Button onClick={() => setEdit(null)}>Abbrechen</Button><Button type="submit" variant="primary">Speichern</Button></div>
          </form>
        ) : (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {[["Personalnummer", String(p.number).padStart(3, "0")], ["Position", p.position ?? "–"], ["Adresse", [p.street, `${p.postal_code ?? ""} ${p.city ?? ""}`.trim()].filter(Boolean).join(", ") || "–"],
              ["Telefon", p.phone ?? "–"], ["Bankverbindung", p.iban ?? "–"], ["Steuerklasse", String(p.tax_class)], ["Krankenkasse", p.health_insurance === "pkv" ? "privat" : p.health_insurer ?? "gesetzlich"],
              ["Urlaubstage", p.vacation_days != null ? String(p.vacation_days) : "–"]].map(([k, v]) => (
              <div key={k}><dt className="text-xs text-slate-500">{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        )}
        <p className="mt-4 text-xs text-slate-500">Steuerklasse, Kinderfreibeträge und Konfession werden vom Finanzamt (ELStAM) übermittelt – Änderungen bitte dort bzw. der Personalabteilung melden.</p>
      </Card>
    </div>
  );
}
