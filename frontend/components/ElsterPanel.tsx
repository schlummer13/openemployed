"use client";

import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "./Feedback";
import { Icon } from "./Icon";
import { Badge, Button, linkButtonClass } from "./ui";
import { api, HttpError } from "@/lib/http";

interface Status { eric_installed: boolean; hersteller_id: boolean; certificate: boolean; test_mode: boolean; state: string }
interface Submission { id: string; test_mode: boolean; status: "validated" | "sent" | "error"; transfer_ticket: string | null; error: string | null; created_by: string; created_at: string }

/** ELSTER-Übertragung der Voranmeldung: Datensatz, ERiC-Prüfung, Versand (Zertifikats-PIN wird nicht gespeichert). */
export function ElsterPanel({ spec, locked }: { spec: string; locked: boolean }) {
  const [data, setData] = useState<{ status: Status; history: Submission[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, prompt, confirm } = useFeedback();
  const load = useCallback(() => { void api<{ status: Status; history: Submission[] }>("GET", `/api/elster/ustva/${spec}`).then(setData); }, [spec]);
  useEffect(load, [load]);
  if (!data) return null;
  const s = data.status;
  const ready = s.eric_installed && s.hersteller_id && s.certificate;

  const validate = async () => {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error: string | null }>("POST", `/api/elster/ustva/${spec}/validate`);
      toast(r.ok ? "ERiC-Prüfung erfolgreich – der Datensatz ist plausibel." : `ERiC meldet: ${r.error}`, r.ok ? "success" : "error");
      load();
    } catch (e) { toast(e instanceof HttpError ? e.message : "Prüfung fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const send = async () => {
    if (!(await confirm({ title: s.test_mode ? "Testübertragung an ELSTER?" : "Voranmeldung an das Finanzamt senden?", confirmLabel: "Weiter",
      tone: s.test_mode ? "primary" : "danger",
      message: s.test_mode ? "Mit Testmerker – das Finanzamt verarbeitet den Fall nicht." : "Die Voranmeldung wird rechtsverbindlich übermittelt." }))) return;
    const pin = await prompt({ title: "PIN des ELSTER-Zertifikats", confirmLabel: "Senden", message: "Die PIN wird nur für diese Übertragung verwendet und nicht gespeichert.",
      input: { label: "PIN", secret: true, minLength: 4 } });
    if (!pin) return;
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; transfer_ticket: string | null; error: string | null }>("POST", `/api/elster/ustva/${spec}/send`, { pin });
      toast(r.ok ? `Übermittelt${r.transfer_ticket ? ` – Transferticket ${r.transfer_ticket}` : ""}.` : `ELSTER meldet: ${r.error}`, r.ok ? "success" : "error");
      load();
    } catch (e) { toast(e instanceof HttpError ? e.message : "Übertragung fehlgeschlagen", "error"); } finally { setBusy(false); }
  };

  const check = (ok: boolean, label: string) => (
    <li className="flex items-center gap-2"><span className={`grid size-4 place-items-center rounded-full ${ok ? "bg-emerald-500 text-white" : "ring-1 ring-inset ring-slate-300 dark:ring-white/20"}`}>{ok && <Icon name="check" className="size-2.5" />}</span>{label}</li>
  );
  return (
    <div className="mt-5 rounded-xl border border-slate-200 p-4 dark:border-white/10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold">An ELSTER übermitteln {s.test_mode && <Badge tone="amber">Testmodus</Badge>}</h3>
          <ul className="mt-2 grid gap-1 text-xs text-slate-600 sm:grid-cols-2 dark:text-slate-300">
            {check(s.eric_installed, "ERiC-Bibliothek installiert")}
            {check(s.hersteller_id, "Hersteller-ID hinterlegt")}
            {check(s.certificate, "ELSTER-Zertifikat vorhanden")}
            {check(locked, "Zeitraum festgeschrieben")}
          </ul>
        </div>
        <div className="flex flex-wrap gap-2">
          <a className={linkButtonClass} href={`/api/elster/ustva/${spec}/xml`}><Icon name="file" />Datensatz (XML)</a>
          <Button onClick={() => void validate()} disabled={busy || !s.eric_installed}>Prüfen</Button>
          <Button variant="primary" onClick={() => void send()} disabled={busy || !ready || !locked}>Senden</Button>
        </div>
      </div>
      {!ready && <p className="mt-3 text-xs text-slate-500">Einrichtung: kostenlose Registrierung im ELSTER-Entwicklerbereich (Hersteller-ID), ERiC nach <code>secrets/eric</code>, Organisationszertifikat als <code>secrets/elster.pfx</code> – Details in docs/betrieb.md. Bis dahin Kennzahlen in „Mein ELSTER“ übertragen.</p>}
      {data.history.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-slate-100 pt-3 text-xs dark:border-white/5">
          {data.history.map((h) => (
            <li key={h.id} className="flex flex-wrap gap-x-2">
              <Badge tone={h.status === "error" ? "red" : h.status === "sent" ? "green" : "blue"}>{h.status === "sent" ? "übermittelt" : h.status === "validated" ? "geprüft" : "Fehler"}</Badge>
              <span>{new Date(h.created_at).toLocaleString("de-DE")} · {h.created_by}{h.test_mode ? " · Test" : ""}{h.transfer_ticket ? ` · Ticket ${h.transfer_ticket}` : ""}</span>
              {h.error && <span className="w-full text-red-600">{h.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
