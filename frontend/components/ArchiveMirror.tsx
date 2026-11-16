"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Drawer, Empty, Field, Input, Select, Skeleton } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

interface Target {
  id: string; name: string; kind: "s3" | "webdav" | "folder"; config: Record<string, string>; object_lock: boolean; lock_mode: string;
  schedule: "daily" | "weekly" | "manual"; enabled: boolean; last_run_at: string | null; last_status: string | null; last_message: string | null;
  last_verified_at: string | null; copies: number; problems: number; documents: number;
}
type Form = { id: string | null; name: string; kind: Target["kind"]; config: Record<string, string>; secret: Record<string, string>; object_lock: boolean;
  lock_mode: string; schedule: Target["schedule"]; enabled: boolean };

const KIND: Record<Target["kind"], { label: string; hint: string }> = {
  s3: { label: "S3-Speicher (optional WORM)", hint: "AWS S3, Wasabi, Backblaze B2, IONOS, Hetzner Object Storage, SeaweedFS …" },
  webdav: { label: "WebDAV", hint: "Nextcloud, ownCloud, Synology/QNAP, Hetzner Storage Box" },
  folder: { label: "Ordner / Festplatte", hint: "Eingehängte USB-Platte oder NAS-Freigabe auf dem Server (ARCHIVE_EXPORT_PATH)" },
};
const dt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");

/** Archiv-Spiegelung auf externe Ziele, Prüfung der Kopien und Vollexport als ZIP. */
export function ArchiveMirror() {
  const [targets, setTargets] = useState<Target[] | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [year, setYear] = useState("");
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<Target[]>("GET", "/api/archive/targets").then(setTargets); }, []);
  useEffect(load, [load]);

  const act = (t: Target, what: "test" | "sync" | "verify") => {
    setBusy(`${t.id}:${what}`);
    api<{ ok?: boolean; message?: string; status?: string; checked?: number; valid?: number; problems?: string[] | number }>("POST", `/api/archive/targets/${t.id}/${what}`)
      .then((r) => {
        if (what === "test") toast(r.message ?? "", r.ok ? "success" : "error");
        else if (what === "sync") toast(`Spiegelung: ${r.message}`, r.status === "ok" ? "success" : "error");
        else toast(r.ok ? `Alle ${r.checked} Kopien geprüft – unverändert.` : `${Array.isArray(r.problems) ? r.problems.length : 0} Kopie(n) fehlen oder weichen ab – werden beim nächsten Lauf neu gespiegelt.`, r.ok ? "success" : "error");
        load();
      })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"))
      .finally(() => setBusy(null));
  };
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    const body = { name: form.name, kind: form.kind, config: form.config, secret: form.secret, object_lock: form.object_lock, lock_mode: form.lock_mode,
      schedule: form.schedule, enabled: form.enabled };
    (form.id ? api("PUT", `/api/archive/targets/${form.id}`, body) : api("POST", "/api/archive/targets", body))
      .then(() => { toast("Ziel gespeichert – jetzt „Verbindung testen“."); setForm(null); load(); })
      .catch((err: unknown) => toast(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen", "error"));
  };
  const remove = async (t: Target) => {
    if (!(await confirm({ title: `Ziel „${t.name}“ entfernen?`, message: "Die Kopien auf dem Ziel bleiben dort erhalten; OpenEmployed spiegelt nur nicht mehr dorthin.", tone: "danger", confirmLabel: "Entfernen" }))) return;
    api("DELETE", `/api/archive/targets/${t.id}`).then(() => { toast("Entfernt."); load(); }).catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"));
  };
  const cfg = (k: string) => form?.config[k] ?? "";
  const setCfg = (k: string, v: string) => form && setForm({ ...form, config: { ...form.config, [k]: v } });
  const setSecret = (k: string, v: string) => form && setForm({ ...form, secret: { ...form.secret, [k]: v } });

  if (!targets) return <Skeleton className="h-64" />;
  const years = Array.from({ length: 11 }, (_, i) => new Date().getFullYear() - i);
  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader title="Spiegelung auf externe Ziele" description="Jede Nacht um 02:00 werden neue Dokumente kopiert und eine Stichprobe der Kopien auf Veränderung geprüft. Fehler erscheinen rot unter „Zu erledigen“."
            actions={<Button variant="primary" onClick={() => setForm({ id: null, name: "", kind: "s3", config: { region: "eu-central-1", prefix: "openemployed" }, secret: {}, object_lock: true, lock_mode: "COMPLIANCE", schedule: "daily", enabled: true })}>+ Ziel</Button>} />
          {targets.length === 0 ? <div className="px-5 pb-5"><Empty>Noch kein Ziel. Empfehlung: ein S3-Speicher mit Object Lock (unveränderbar) und zusätzlich eine Festplatte im Büro.</Empty></div> : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {targets.map((t) => (
                <li key={t.id} className="space-y-2 px-5 py-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{t.name}</span>
                    <Badge tone="slate">{KIND[t.kind].label.split(" (")[0]}</Badge>
                    {t.object_lock && <Badge tone="green">WORM · {t.lock_mode}</Badge>}
                    {!t.enabled && <Badge tone="amber">pausiert</Badge>}
                    {t.last_status === "error" && <Badge tone="red">Fehler</Badge>}
                    <span className="ml-auto text-xs text-slate-500">{t.schedule === "daily" ? "täglich" : t.schedule === "weekly" ? "wöchentlich (Mo)" : "nur manuell"}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"><div className="h-full bg-brand-500" style={{ width: `${t.documents ? Math.min(100, (t.copies / t.documents) * 100) : 100}%` }} /></div>
                  <p className="text-xs text-slate-500">
                    {t.copies} von {t.documents} Dokumenten gespiegelt{t.problems ? ` · ${t.problems} fehlerhaft` : ""} · letzter Lauf {dt(t.last_run_at)}{t.last_message ? ` (${t.last_message})` : ""} · geprüft {dt(t.last_verified_at)}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    <Button className="!px-2.5 !py-1 text-xs" disabled={!!busy} onClick={() => act(t, "test")}>Verbindung testen</Button>
                    <Button className="!px-2.5 !py-1 text-xs" disabled={!!busy} onClick={() => act(t, "sync")}>{busy === `${t.id}:sync` ? "Spiegelt …" : "Jetzt spiegeln"}</Button>
                    <Button className="!px-2.5 !py-1 text-xs" disabled={!!busy} onClick={() => act(t, "verify")}>{busy === `${t.id}:verify` ? "Prüft …" : "Alle Kopien prüfen"}</Button>
                    <Button className="!px-2.5 !py-1 text-xs" onClick={() => setForm({ id: t.id, name: t.name, kind: t.kind, config: t.config, secret: {}, object_lock: t.object_lock, lock_mode: t.lock_mode, schedule: t.schedule, enabled: t.enabled })}>Bearbeiten</Button>
                    <Button className="!px-2.5 !py-1 text-xs" onClick={() => void remove(t)}>Entfernen</Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Auf Festplatte herunterladen" description="Vollständiger, prüfbarer Export als ZIP: Originaldateien je Jahr/Kategorie, Manifest mit Prüfsummen und Hash-Kette, Prüfprogramm (pruefen.py) und durchsuchbare Übersicht (index.html)." />
            <div className="flex flex-wrap items-end gap-3 px-5 pb-5">
              <Field label="Umfang"><Select value={year} onChange={(e) => setYear(e.target.value)} className="!w-40"><option value="">alle Jahre</option>{years.map((y) => <option key={y} value={y}>{y}</option>)}</Select></Field>
              <a className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700" href={`/api/archive/export${year ? `?year=${year}` : ""}`}>ZIP herunterladen</a>
              <a className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-300" href="/api/archive/manifest.csv">nur Manifest (CSV)</a>
            </div>
          </Card>
          <Alert kind="info">Für die Betriebsprüfung (§ 147 Abs. 6 AO) zusätzlich den GoBD-Export der Buchhaltung beilegen. Verlagerung ins Ausland nur mit Genehmigung (§ 146 Abs. 2a AO) – S3-Region in der EU wählen.</Alert>
        </div>
      </div>

      <Drawer open={!!form} onClose={() => setForm(null)} size="lg" title={form?.id ? "Ziel bearbeiten" : "Neues Spiegelziel"} subtitle={form ? KIND[form.kind].hint : undefined}
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button variant="primary" type="submit" form="target-form">Speichern</Button></>}>
        {form && (
          <form id="target-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Name"><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="z. B. Hetzner WORM" /></Field>
            <Field label="Art"><Select value={form.kind} disabled={!!form.id} onChange={(e) => setForm({ ...form, kind: e.target.value as Target["kind"], object_lock: e.target.value === "s3" })}>
              {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select></Field>
            {form.kind === "s3" && <>
              <Field label="Endpoint" hint="https://… des Anbieters" className="sm:col-span-2"><Input required value={cfg("endpoint")} onChange={(e) => setCfg("endpoint", e.target.value)} placeholder="https://fsn1.your-objectstorage.com" /></Field>
              <Field label="Bucket"><Input required value={cfg("bucket")} onChange={(e) => setCfg("bucket", e.target.value)} /></Field>
              <Field label="Region"><Input value={cfg("region")} onChange={(e) => setCfg("region", e.target.value)} placeholder="eu-central-1" /></Field>
              <Field label="Access Key"><Input autoComplete="off" value={form.secret.access_key ?? ""} onChange={(e) => setSecret("access_key", e.target.value)} placeholder={form.id ? "unverändert" : ""} /></Field>
              <Field label="Secret Key"><Input type="password" autoComplete="new-password" value={form.secret.secret_key ?? ""} onChange={(e) => setSecret("secret_key", e.target.value)} placeholder={form.id ? "unverändert" : ""} /></Field>
              <Field label="Präfix (Ordner im Bucket)"><Input value={cfg("prefix")} onChange={(e) => setCfg("prefix", e.target.value)} /></Field>
              <label className="flex items-start gap-2 text-sm sm:col-span-2">
                <input type="checkbox" className="mt-0.5" checked={form.object_lock} disabled={!!form.id && form.object_lock} onChange={(e) => setForm({ ...form, object_lock: e.target.checked })} />
                <span><strong>Object Lock (WORM)</strong> – jede Kopie ist bis zum Ende ihrer Aufbewahrungsfrist unlöschbar und unveränderbar, auch für Administratoren.
                  <span className="block text-xs text-slate-500">Der Bucket muss beim Anlegen mit „Object Lock“ erstellt werden; einmal aktiv, lässt es sich hier nicht mehr abschalten.</span></span>
              </label>
              {form.object_lock && <Field label="Sperrmodus"><Select value={form.lock_mode} onChange={(e) => setForm({ ...form, lock_mode: e.target.value })}>
                <option value="COMPLIANCE">COMPLIANCE (empfohlen, niemand kann löschen)</option><option value="GOVERNANCE">GOVERNANCE (mit Sonderrecht löschbar)</option></Select></Field>}
            </>}
            {form.kind === "webdav" && <>
              <Field label="WebDAV-Adresse" className="sm:col-span-2" hint="Nextcloud: https://cloud.example.de/remote.php/dav/files/BENUTZER/Archiv"><Input required value={cfg("url")} onChange={(e) => setCfg("url", e.target.value)} /></Field>
              <Field label="Benutzer"><Input value={cfg("username")} onChange={(e) => setCfg("username", e.target.value)} /></Field>
              <Field label="Passwort / App-Passwort"><Input type="password" autoComplete="new-password" value={form.secret.password ?? ""} onChange={(e) => setSecret("password", e.target.value)} placeholder={form.id ? "unverändert" : ""} /></Field>
              <Field label="Präfix (Unterordner)"><Input value={cfg("prefix")} onChange={(e) => setCfg("prefix", e.target.value)} placeholder="openemployed" /></Field>
            </>}
            {form.kind === "folder" && <>
              <Field label="Unterordner" hint="innerhalb von ARCHIVE_EXPORT_PATH auf dem Server"><Input value={cfg("subfolder")} onChange={(e) => setCfg("subfolder", e.target.value)} placeholder="archiv" /></Field>
              <p className="text-xs text-slate-500 sm:col-span-2">Dateien werden schreibgeschützt abgelegt. Für eine USB-Platte: Platte einhängen (z. B. <code>/mnt/usb-archiv</code>), in der <code>.env</code> <code>ARCHIVE_EXPORT_PATH=/mnt/usb-archiv</code> setzen und <code>make update</code> ausführen.</p>
            </>}
            <Field label="Zeitplan"><Select value={form.schedule} onChange={(e) => setForm({ ...form, schedule: e.target.value as Target["schedule"] })}>
              <option value="daily">täglich (02:00)</option><option value="weekly">wöchentlich (Montag)</option><option value="manual">nur manuell</option></Select></Field>
            <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> aktiv</label>
          </form>
        )}
      </Drawer>
    </div>
  );
}
