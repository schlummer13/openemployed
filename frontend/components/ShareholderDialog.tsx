"use client";

import { useState, type FormEvent } from "react";
import { Avatar } from "@/components/Avatar";
import { Alert, Button, Field, Input, Select } from "@/components/ui";
import { toApiDecimal } from "@/lib/api";
import { api, HttpError } from "@/lib/http";

export interface HolderData {
  id: string; name: string; email: string | null; share_percent: string; is_managing_director: boolean; phone: string | null;
  birth_date: string | null; street: string | null; postal_code: string | null; city: string | null; tax_id: string | null;
  social_security_number: string | null; iban: string | null; role_title: string | null; joined_on: string | null; color: string;
  has_photo: boolean; updated_at?: string;
  tax_profile: { valid_from: string; tax_class: number; child_allowances: string; church_tax: boolean; state: string; birth_year: number | null;
    children_under_25: number; health_insurance: "gkv" | "pkv"; health_insurer: string | null; kv_additional_rate: string | null; pkv_premium: string } | null;
}

const STATES: [string, string][] = [["BW", "Baden-Württemberg"], ["BY", "Bayern"], ["BE", "Berlin"], ["BB", "Brandenburg"], ["HB", "Bremen"], ["HH", "Hamburg"], ["HE", "Hessen"], ["MV", "Mecklenburg-Vorpommern"], ["NI", "Niedersachsen"], ["NW", "Nordrhein-Westfalen"], ["RP", "Rheinland-Pfalz"], ["SL", "Saarland"], ["SN", "Sachsen"], ["ST", "Sachsen-Anhalt"], ["SH", "Schleswig-Holstein"], ["TH", "Thüringen"]];
const ROMAN = ["", "I", "II", "III", "IV", "V", "VI"];
const COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#0ea5e9", "#a855f7", "#ec4899", "#14b8a6"];

function nextMonthStart(): string {
  const d = new Date();
  const n = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-01`;
}
const de = (v: string | null | undefined) => (v ? v.replace(".", ",") : "");

export function ShareholderDialog({ holder, onClose, onSaved }: { holder: HolderData | null; onClose: () => void; onSaved: (msg: string) => void }) {
  const p = holder?.tax_profile;
  const [tab, setTab] = useState<"person" | "steuer" | "konto">("person");
  const [m, setM] = useState({
    name: holder?.name ?? "", role_title: holder?.role_title ?? "Geschäftsführer/in", email: holder?.email ?? "", phone: holder?.phone ?? "",
    birth_date: holder?.birth_date ?? "", street: holder?.street ?? "", postal_code: holder?.postal_code ?? "", city: holder?.city ?? "",
    joined_on: holder?.joined_on ?? "", share_percent: de(holder?.share_percent ?? "25"), is_managing_director: holder?.is_managing_director ?? true,
    color: holder?.color ?? COLORS[0], tax_id: holder?.tax_id ?? "", social_security_number: holder?.social_security_number ?? "", iban: holder?.iban ?? "",
  });
  const [t, setT] = useState({
    valid_from: nextMonthStart(), tax_class: p?.tax_class ?? 1, child_allowances: de(p?.child_allowances ?? "0"), church_tax: p?.church_tax ?? false,
    state: p?.state ?? "NW", children_under_25: p?.children_under_25 ?? 0, health_insurance: p?.health_insurance ?? "gkv",
    health_insurer: p?.health_insurer ?? "", kv_additional_rate: de(p?.kv_additional_rate), pkv_premium: p && Number(p.pkv_premium) ? de(p.pkv_premium) : "",
  });
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const taxChanged = !p || JSON.stringify({ ...t, valid_from: "" }) !== JSON.stringify({
    valid_from: "", tax_class: p.tax_class, child_allowances: de(p.child_allowances), church_tax: p.church_tax, state: p.state,
    children_under_25: p.children_under_25, health_insurance: p.health_insurance, health_insurer: p.health_insurer ?? "",
    kv_additional_rate: de(p.kv_additional_rate), pkv_premium: Number(p.pkv_premium) ? de(p.pkv_premium) : "" });

  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const body = { ...m, share_percent: toApiDecimal(m.share_percent), birth_date: m.birth_date || null, joined_on: m.joined_on || null,
        email: m.email || null, tax_id: m.tax_id || null, social_security_number: m.social_security_number || null, iban: m.iban || null };
      let id = holder?.id;
      if (id) await api("PUT", `/api/shareholders/${id}`, body);
      else id = (await api<{ id: string }>("POST", "/api/shareholders", body)).id;
      if (taxChanged) {
        await api("POST", `/api/shareholders/${id}/tax-profile`, {
          ...t, child_allowances: toApiDecimal(t.child_allowances || "0"), birth_year: m.birth_date ? Number(m.birth_date.slice(0, 4)) : null,
          health_insurer: t.health_insurer || null, kv_additional_rate: t.kv_additional_rate ? toApiDecimal(t.kv_additional_rate) : null,
          pkv_premium: t.health_insurance === "pkv" && t.pkv_premium ? toApiDecimal(t.pkv_premium) : "0",
          valid_from: !p && m.joined_on ? m.joined_on : t.valid_from,
        });
      }
      if (photo) {
        const fd = new FormData(); fd.append("file", photo);
        const res = await fetch(`/api/shareholders/${id}/photo`, { method: "PUT", body: fd });
        if (!res.ok) { const b = (await res.json().catch(() => ({}))) as { detail?: string }; throw new Error(b.detail ?? "Foto konnte nicht gespeichert werden"); }
      }
      onSaved(`${m.name} gespeichert.`);
    } catch (err) {
      setError(err instanceof HttpError ? err.message : err instanceof Error ? err.message : "Speichern fehlgeschlagen");
    } finally { setBusy(false); }
  }

  const tabBtn = (key: typeof tab, label: string) => (
    <button type="button" onClick={() => setTab(key)} className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition ${tab === key ? "bg-white text-slate-950 shadow-card dark:bg-slate-800 dark:text-white" : "text-slate-500 hover:text-slate-900 dark:hover:text-white"}`}>{label}</button>
  );

  return (
    <div role="dialog" aria-modal="true" aria-label="Gesellschafter bearbeiten" className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4 backdrop-blur-sm" onClick={onClose}>
      <form onSubmit={save} onClick={(e) => e.stopPropagation()} className="w-full max-w-2xl rounded-2xl bg-white p-6 shadow-pop dark:bg-slate-900">
        <div className="mb-5 flex items-center gap-4">
          <label className="group relative cursor-pointer" title="Foto ändern">
            {preview ? <img src={preview} alt="" className="size-16 rounded-full object-cover" />
              : <Avatar person={{ id: holder?.id ?? "neu", name: m.name || "?", color: m.color, has_photo: holder?.has_photo }} size={64} version={holder?.updated_at} />}
            <span className="absolute inset-0 grid place-items-center rounded-full bg-black/40 text-xs font-medium text-white opacity-0 transition group-hover:opacity-100">Foto</span>
            <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" aria-label="Foto hochladen"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) { setPhoto(f); setPreview(URL.createObjectURL(f)); } }} />
          </label>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold">{holder ? m.name || "Gesellschafter" : "Neuer Gesellschafter"}</h2>
            <p className="text-sm text-slate-500">{m.role_title} · {m.share_percent || "0"} % Anteil</p>
          </div>
          <div className="flex gap-1">{COLORS.map((c) => <button key={c} type="button" aria-label={`Farbe ${c}`} onClick={() => setM({ ...m, color: c })} className={`size-5 rounded-full ${m.color === c ? "ring-2 ring-offset-2 ring-slate-400 dark:ring-offset-slate-900" : ""}`} style={{ background: c }} />)}</div>
        </div>
        <div className="mb-4 inline-flex gap-1 rounded-xl bg-slate-100 p-1 dark:bg-slate-950">{tabBtn("person", "Person")}{tabBtn("steuer", "Lohnsteuer & SV")}{tabBtn("konto", "Kennungen & Konto")}</div>
        {error && <div className="mb-3"><Alert>{error}</Alert></div>}

        {tab === "person" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name"><Input required value={m.name} onChange={(e) => setM({ ...m, name: e.target.value })} /></Field>
            <Field label="Funktion"><Input value={m.role_title} onChange={(e) => setM({ ...m, role_title: e.target.value })} /></Field>
            <Field label="E-Mail"><Input type="email" value={m.email} onChange={(e) => setM({ ...m, email: e.target.value })} /></Field>
            <Field label="Telefon"><Input value={m.phone} onChange={(e) => setM({ ...m, phone: e.target.value })} /></Field>
            <Field label="Geburtsdatum"><Input type="date" value={m.birth_date} onChange={(e) => setM({ ...m, birth_date: e.target.value })} /></Field>
            <Field label="Eintritt / Bestellung zum GF"><Input type="date" value={m.joined_on} onChange={(e) => setM({ ...m, joined_on: e.target.value })} /></Field>
            <Field label="Straße" className="sm:col-span-2"><Input value={m.street} onChange={(e) => setM({ ...m, street: e.target.value })} /></Field>
            <Field label="PLZ"><Input inputMode="numeric" maxLength={5} value={m.postal_code} onChange={(e) => setM({ ...m, postal_code: e.target.value })} /></Field>
            <Field label="Ort"><Input value={m.city} onChange={(e) => setM({ ...m, city: e.target.value })} /></Field>
            <Field label="Geschäftsanteil (%)"><Input required inputMode="decimal" value={m.share_percent} onChange={(e) => setM({ ...m, share_percent: e.target.value })} /></Field>
            <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={m.is_managing_director} onChange={(e) => setM({ ...m, is_managing_director: e.target.checked })} /> ist Geschäftsführer/in</label>
          </div>
        )}
        {tab === "steuer" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Steuerklasse"><Select value={t.tax_class} onChange={(e) => setT({ ...t, tax_class: Number(e.target.value) })}>{[1, 2, 3, 4, 5, 6].map((k) => <option key={k} value={k}>{ROMAN[k]}{k === 2 ? " (alleinerziehend)" : k === 3 ? " (verheiratet, Splitting)" : k === 5 ? " (verheiratet, Partner III)" : k === 6 ? " (weiteres Arbeitsverhältnis)" : ""}</option>)}</Select></Field>
            <Field label="Kinderfreibeträge (ELStAM)"><Input inputMode="decimal" value={t.child_allowances} onChange={(e) => setT({ ...t, child_allowances: e.target.value })} /></Field>
            <Field label="Kinder unter 25 (Pflegeversicherung)"><Input type="number" min={0} max={20} value={t.children_under_25} onChange={(e) => setT({ ...t, children_under_25: Number(e.target.value) })} /></Field>
            <Field label="Bundesland (Wohnsitz)"><Select value={t.state} onChange={(e) => setT({ ...t, state: e.target.value })}>{STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
            <Field label="Krankenversicherung"><Select value={t.health_insurance} onChange={(e) => setT({ ...t, health_insurance: e.target.value as "gkv" | "pkv" })}><option value="gkv">gesetzlich</option><option value="pkv">privat</option></Select></Field>
            <Field label="Kasse / Versicherer"><Input value={t.health_insurer} onChange={(e) => setT({ ...t, health_insurer: e.target.value })} /></Field>
            {t.health_insurance === "gkv"
              ? <Field label="Zusatzbeitrag der Kasse (%)" hint="leer = Durchschnitt"><Input inputMode="decimal" value={t.kv_additional_rate} onChange={(e) => setT({ ...t, kv_additional_rate: e.target.value })} /></Field>
              : <Field label="PKV-Beitrag inkl. PPV (€/Monat)"><Input inputMode="decimal" value={t.pkv_premium} onChange={(e) => setT({ ...t, pkv_premium: e.target.value })} /></Field>}
            <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={t.church_tax} onChange={(e) => setT({ ...t, church_tax: e.target.checked })} /> kirchensteuerpflichtig</label>
            {p && taxChanged && <Field label="Änderung gültig ab" hint="frühere Stände bleiben erhalten"><Input type="date" required value={t.valid_from} onChange={(e) => setT({ ...t, valid_from: e.target.value })} /></Field>}
            <p className="text-xs text-slate-500 sm:col-span-2">Merkmale laut ELStAM (Mein ELSTER) und Mitgliedsbescheinigung der Kasse. Das Geburtsjahr wird aus dem Geburtsdatum übernommen (Pflegeversicherung ab 23 Jahren).</p>
          </div>
        )}
        {tab === "konto" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Steuerliche Identifikationsnummer" hint="11 Ziffern, mit Prüfziffer"><Input inputMode="numeric" autoComplete="off" value={m.tax_id} onChange={(e) => setM({ ...m, tax_id: e.target.value })} /></Field>
            <Field label="Rentenversicherungsnummer" hint="z. B. 12 010490 B 123"><Input autoComplete="off" value={m.social_security_number} onChange={(e) => setM({ ...m, social_security_number: e.target.value.toUpperCase() })} /></Field>
            <Field label="IBAN für die Gehaltszahlung" className="sm:col-span-2"><Input autoComplete="off" value={m.iban} onChange={(e) => setM({ ...m, iban: e.target.value.toUpperCase() })} /></Field>
            <p className="text-xs text-slate-500 sm:col-span-2">Diese Angaben braucht die Lohnabrechnung (Steuerberater). Im Änderungsprotokoll werden sie nur maskiert gespeichert.</p>
          </div>
        )}
        <div className="mt-6 flex justify-end gap-2"><Button onClick={onClose}>Abbrechen</Button><Button type="submit" variant="primary" disabled={busy}>{busy ? "Speichere …" : "Speichern"}</Button></div>
      </form>
    </div>
  );
}
