"use client";

import { DocumensoPanel } from "@/components/DocumensoPanel";
import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Empty, Field, Input, PageHeader, PageSkeleton, Select, Table, Tabs } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface F { key: string; group: string; label: string; kind: "text" | "secret" | "bool" | "int" | "select"; options: string[]; hint: string; value: unknown; source: "app" | "env" }
interface Key { id: string; name: string; prefix: string; role: string; created_by: string; created_at: string; last_used_at: string | null; revoked_at: string | null; expires_at: string | null }
interface Hook { id: string; url: string; description: string | null; events: string[]; active: boolean; failed: number; last_delivery: string | null }
interface Delivery { id: string; endpoint_id: string; event: string; status: string; attempts: number; response_code: number | null; last_error: string | null; created_at: string }
interface Sys { version: string; env: string; update_command: string; restore_command: string; backups: { available: boolean; files: { kind: string; name: string; size: number; created: string; verified: boolean }[]; last_success: string | null; last_failure: string | null; requested: boolean } }

const GROUPS: Record<string, string> = { mail: "E-Mail", login: "Anmeldung", tax: "Steuern", einvoice: "E-Rechnung (Peppol)", signature: "Qualifizierte Signatur", ai: "KI" };
const mb = (b: number) => `${(b / 1024 / 1024).toFixed(1)} MB`;

export default function SystemPage() {
  const [tab, setTab] = useState<"settings" | "currencies" | "api" | "webhooks" | "system">("settings");
  return (
    <>
      <PageHeader title="System & Integrationen" eyebrow="System" description="Einstellungen ohne .env-Bearbeitung, Schnittstellen für eigene Tools (REST-API, Webhooks) sowie Version und Datensicherung." />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[{ value: "settings", label: "Einstellungen" }, { value: "currencies", label: "Währungen" }, { value: "api", label: "API-Schlüssel" }, { value: "webhooks", label: "Webhooks" }, { value: "system", label: "Version & Sicherung" }]} /></div>
      {tab === "settings" && <div className="space-y-6"><DocumensoPanel /><Settings /></div>}
      {tab === "currencies" && <Currencies />}
      {tab === "api" && <ApiKeys />}
      {tab === "webhooks" && <Webhooks />}
      {tab === "system" && <SystemInfo />}
    </>
  );
}

function Settings() {
  const [data, setData] = useState<{ fields: F[]; redirect_uris: Record<string, string> } | null>(null);
  const [group, setGroup] = useState("mail");
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<{ fields: F[]; redirect_uris: Record<string, string> }>("GET", "/api/admin/settings").then((d) => { setData(d); setDraft({}); }); }, []);
  useEffect(load, [load]);
  if (!data) return <PageSkeleton stats={0} />;
  const fields = data.fields.filter((f) => f.group === group);
  const value = (f: F) => (f.key in draft ? draft[f.key] : f.kind === "secret" ? "" : f.value ?? "");
  const save = async () => {
    setBusy(true);
    try { await api("PUT", "/api/admin/settings", { changes: draft }); toast("Einstellungen gespeichert und sofort aktiv."); load(); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Speichern fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const testMail = async () => {
    try { const r = await api<{ to: string }>("POST", "/api/mail/test"); toast(`Testmail an ${r.to} gesendet.`); } catch (e) { toast(e instanceof HttpError ? e.message : "Versand fehlgeschlagen", "error"); }
  };
  return (
    <Card>
      <div className="mb-5 flex flex-wrap gap-1.5">
        {Object.entries(GROUPS).map(([k, l]) => <button key={k} type="button" onClick={() => setGroup(k)} className={`rounded-full px-3 py-1 text-xs font-medium ${group === k ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-white/5 dark:text-slate-300"}`}>{l}</button>)}
      </div>
      {group === "login" && <div className="mb-4"><Alert kind="info">Weiterleitungs-URIs für die App-Registrierung: Microsoft <code className="break-all">{data.redirect_uris.microsoft}</code> · OIDC <code className="break-all">{data.redirect_uris.oidc}</code></Alert></div>}
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map((f) => (
          <Field key={f.key} label={f.label} hint={[f.hint, f.source === "app" ? "in der App gesetzt" : "aus .env"].filter(Boolean).join(" · ")}>
            {f.kind === "bool" ? (
              <label className="flex h-9 items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(value(f))} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.checked })} />aktiv</label>
            ) : f.kind === "select" ? (
              <Select value={String(value(f))} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}>{f.options.map((o) => <option key={o}>{o}</option>)}</Select>
            ) : (
              <Input type={f.kind === "secret" ? "password" : f.kind === "int" ? "number" : "text"} autoComplete="off" placeholder={f.kind === "secret" ? (f.value ? "•••••••• (gesetzt – leer lassen = unverändert)" : "nicht gesetzt") : ""}
                value={String(value(f))} onChange={(e) => setDraft({ ...draft, [f.key]: f.kind === "int" ? Number(e.target.value) : e.target.value })} />
            )}
          </Field>
        ))}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {group === "mail" && <Button onClick={() => void testMail()}>Testmail an mich</Button>}
        <Button variant="primary" disabled={busy || Object.keys(draft).length === 0} onClick={() => void save()}>Speichern</Button>
      </div>
    </Card>
  );
}

function ApiKeys() {
  const [keys, setKeys] = useState<Key[] | null>(null);
  const [form, setForm] = useState({ name: "", role: "advisor", days: "" });
  const [created, setCreated] = useState<string | null>(null);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<Key[]>("GET", "/api/admin/api-keys").then(setKeys); }, []);
  useEffect(load, [load]);
  const create = async () => {
    try { const r = await api<{ key: string }>("POST", "/api/admin/api-keys", { name: form.name, role: form.role, days: form.days ? Number(form.days) : null }); setCreated(r.key); setForm({ name: "", role: "advisor", days: "" }); load(); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"); }
  };
  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader title="Neuer API-Schlüssel" description="REST-API unter /api/… mit Header „Authorization: Bearer sl_…“ – z. B. für n8n, Zapier, Make oder eigene Skripte. Dokumentation: /api/docs (lokal)." />
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Name" className="w-56"><Input value={form.name} placeholder="z. B. n8n" onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Rechte" className="w-56"><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="advisor">nur lesen</option><option value="accounting">lesen und schreiben</option></Select></Field>
          <Field label="Gültig (Tage)" className="w-32"><Input type="number" placeholder="unbegrenzt" value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })} /></Field>
          <Button variant="primary" disabled={!form.name} onClick={() => void create()}>Erzeugen</Button>
        </div>
        {created && <div className="mt-4"><Alert kind="warning">Schlüssel jetzt kopieren – er wird nicht noch einmal angezeigt: <code className="break-all font-mono">{created}</code></Alert></div>}
      </Card>
      <Card>{!keys || keys.length === 0 ? <Empty>Keine API-Schlüssel.</Empty> : (
        <Table head={<tr><th>Name</th><th>Schlüssel</th><th>Rechte</th><th>Zuletzt genutzt</th><th /></tr>}>
          {keys.map((k) => <tr key={k.id} className={k.revoked_at ? "opacity-50" : ""}>
            <td className="font-medium">{k.name}<span className="block text-xs font-normal text-slate-500">{k.created_by} · {formatDate(k.created_at)}</span></td>
            <td className="font-mono text-xs">sl_{k.prefix}_…</td><td>{k.role === "advisor" ? "lesen" : "lesen + schreiben"}</td>
            <td className="text-xs">{k.last_used_at ? formatDate(k.last_used_at) : "nie"}{k.expires_at ? ` · bis ${formatDate(k.expires_at)}` : ""}</td>
            <td className="text-right">{k.revoked_at ? <Badge>widerrufen</Badge> : <Button variant="ghost" className="!h-7 !text-xs" onClick={async () => { if (await confirm(`Schlüssel „${k.name}“ widerrufen?\n\nIntegrationen mit diesem Schlüssel funktionieren sofort nicht mehr.`)) { await api("DELETE", `/api/admin/api-keys/${k.id}`); load(); } }}>Widerrufen</Button>}</td>
          </tr>)}
        </Table>
      )}</Card>
    </div>
  );
}

function Webhooks() {
  const [data, setData] = useState<{ endpoints: Hook[]; deliveries: Delivery[]; events: Record<string, string> } | null>(null);
  const [form, setForm] = useState<{ url: string; events: string[] }>({ url: "", events: [] });
  const [secret, setSecret] = useState<string | null>(null);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<{ endpoints: Hook[]; deliveries: Delivery[]; events: Record<string, string> }>("GET", "/api/admin/webhooks").then(setData); }, []);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); toast(msg); load(); } catch (e) { toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"); } };
  if (!data) return <PageSkeleton stats={0} />;
  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader title="Neuer Webhook" description="Bei jedem gewählten Ereignis sendet OpenEmployed ein signiertes JSON (Header X-OpenEmployed-Signature: t=…,v1=HMAC-SHA256) per POST." />
        <div className="grid gap-3">
          <Field label="Ziel-URL"><Input placeholder="https://n8n.firma.de/webhook/…" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></Field>
          <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(data.events).map(([k, l]) => <label key={k} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.events.includes(k)} onChange={(e) => setForm({ ...form, events: e.target.checked ? [...form.events, k] : form.events.filter((x) => x !== k) })} /><span>{l}<span className="block font-mono text-[11px] text-slate-500">{k}</span></span></label>)}
          </div>
          <div><Button variant="primary" disabled={!form.url || form.events.length === 0} onClick={() => void act(async () => { const r = await api<{ secret: string }>("POST", "/api/admin/webhooks", { url: form.url, events: form.events }); setSecret(r.secret); setForm({ url: "", events: [] }); }, "Webhook angelegt.")}>Anlegen</Button></div>
          {secret && <Alert kind="warning">Signatur-Geheimnis jetzt kopieren: <code className="break-all font-mono">{secret}</code></Alert>}
        </div>
      </Card>
      <Card>{data.endpoints.length === 0 ? <Empty>Keine Webhooks.</Empty> : (
        <Table head={<tr><th>Ziel</th><th>Ereignisse</th><th>Status</th><th /></tr>}>
          {data.endpoints.map((h) => <tr key={h.id}>
            <td className="max-w-[18rem] truncate font-mono text-xs">{h.url}</td>
            <td className="text-xs">{h.events.join(", ")}</td>
            <td>{h.active ? <Badge tone="green">aktiv</Badge> : <Badge>pausiert</Badge>}{h.failed > 0 && <Badge tone="red">{h.failed} fehlgeschlagen</Badge>}</td>
            <td className="whitespace-nowrap text-right">
              <Button variant="ghost" className="!h-7 !text-xs" onClick={() => void act(() => api("POST", `/api/admin/webhooks/${h.id}/test`), "Testereignis gesendet.")}>Test</Button>
              <Button variant="ghost" className="!h-7 !text-xs" onClick={() => void act(() => api("PUT", `/api/admin/webhooks/${h.id}`, { active: !h.active }), h.active ? "Pausiert." : "Aktiviert.")}>{h.active ? "Pausieren" : "Aktivieren"}</Button>
              <Button variant="ghost" className="!h-7 !text-xs" onClick={async () => { if (await confirm(`Webhook löschen?\n\n${h.url}`)) void act(() => api("DELETE", `/api/admin/webhooks/${h.id}`), "Gelöscht."); }}>Löschen</Button>
            </td>
          </tr>)}
        </Table>
      )}</Card>
      {data.deliveries.length > 0 && <Card>
        <CardHeader title="Letzte Zustellungen" />
        <Table head={<tr><th>Zeit</th><th>Ereignis</th><th>Status</th><th>Antwort</th></tr>}>
          {data.deliveries.map((d) => <tr key={d.id}><td className="text-xs tabular">{new Date(d.created_at).toLocaleString("de-DE")}</td><td className="font-mono text-xs">{d.event}</td>
            <td><Badge tone={d.status === "delivered" ? "green" : d.status === "failed" ? "red" : "amber"}>{d.status === "delivered" ? "zugestellt" : d.status === "failed" ? "fehlgeschlagen" : `offen (${d.attempts} Versuche)`}</Badge></td>
            <td className="max-w-[16rem] truncate text-xs text-slate-500">{d.response_code ?? ""} {d.last_error ?? ""}</td></tr>)}
        </Table>
      </Card>}
    </div>
  );
}

function SystemInfo() {
  const [sys, setSys] = useState<Sys | null>(null);
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<Sys>("GET", "/api/admin/system").then(setSys); }, []);
  useEffect(load, [load]);
  if (!sys) return <PageSkeleton stats={0} />;
  const b = sys.backups;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title={`OpenEmployed ${sys.version}`} description={`Umgebung: ${sys.env}`} />
        <p className="text-sm">Aktualisieren auf dem Server (Sicherung → neue Version → Migrationen):</p>
        <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 text-xs text-slate-100">{sys.update_command}</pre>
        <p className="mt-4 text-sm">Wiederherstellen einer Sicherung (bewusst nur auf der Kommandozeile):</p>
        <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 text-xs text-slate-100">{sys.restore_command}</pre>
      </Card>
      <Card>
        <CardHeader title="Datensicherung" description="Täglich automatisch (30 tägliche, 12 monatliche, 10 jährliche), jede Sicherung wird geprüft und mit SHA-256 versehen."
          actions={<Button disabled={!b.available || b.requested} onClick={async () => { try { await api("POST", "/api/admin/system/backup"); toast("Sicherung angefordert – startet innerhalb von 30 Sekunden."); load(); } catch (e) { toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"); } }}>{b.requested ? "angefordert …" : "Jetzt sichern"}</Button>} />
        {!b.available ? <Alert kind="warning">Sicherungsverzeichnis ist im Backend nicht eingebunden (docker-compose: Volume /backups).</Alert> : <>
          <p className="mb-3 text-sm">Letzte erfolgreiche Sicherung: <strong>{b.last_success ? new Date(b.last_success).toLocaleString("de-DE") : "noch keine"}</strong>{b.last_failure && <span className="text-red-600"> · letzter Fehler {new Date(b.last_failure).toLocaleString("de-DE")}</span>}</p>
          <ul className="max-h-72 divide-y divide-slate-100 overflow-y-auto text-sm dark:divide-white/5">
            {b.files.map((f) => <li key={f.kind + f.name} className="flex items-center gap-3 py-1.5"><span className="min-w-0 flex-1 truncate font-mono text-xs">{f.name}</span><Badge>{f.kind}</Badge><span className="text-xs tabular text-slate-500">{mb(f.size)}</span><a className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-300" href={`/api/admin/system/backups/${encodeURIComponent(f.name)}`}>Download</a></li>)}
          </ul>
          <p className="mt-3 text-xs text-slate-500">Zusätzlich eine Kopie außer Haus ablegen (z. B. Storage Box per rsync) – siehe docs/betrieb.md.</p>
        </>}
      </Card>
    </div>
  );
}

function Currencies() {
  const [data, setData] = useState<{ currencies: string[]; rates: { currency: string; rate_date: string; rate: string; source: string }[] } | null>(null);
  const [form, setForm] = useState({ currency: "USD", rate: "" });
  const { toast } = useFeedback();
  const load = useCallback(() => { void api<{ currencies: string[]; rates: { currency: string; rate_date: string; rate: string; source: string }[] }>("GET", "/api/currencies").then(setData); }, []);
  useEffect(load, [load]);
  if (!data) return <PageSkeleton stats={0} />;
  return (
    <Card>
      <CardHeader title="Wechselkurse" description="Rechnungen in Fremdwährung werden zum EZB-Referenzkurs des Rechnungsdatums in Euro gebucht; der Steuerbetrag steht zusätzlich in Euro auf der Rechnung. Kurse werden täglich automatisch geholt, sobald ein Kunde in Fremdwährung angelegt ist."
        actions={<Button onClick={async () => { try { const r = await api<{ rates: number }>("POST", "/api/currencies/fetch"); toast(`${r.rates} EZB-Kurse geladen.`); load(); } catch (e) { toast(e instanceof HttpError ? e.message : "Abruf fehlgeschlagen", "error"); } }}>EZB-Kurse abrufen</Button>} />
      {data.rates.length === 0 ? <Empty>Noch keine Kurse.</Empty> : (
        <Table head={<tr><th>Währung</th><th className="text-right">1 EUR =</th><th>Stand</th><th>Quelle</th></tr>}>
          {data.rates.map((r) => <tr key={r.currency}><td className="font-medium">{r.currency}</td><td className="text-right tabular">{Number(r.rate).toLocaleString("de-DE", { maximumFractionDigits: 6 })}</td><td className="tabular">{formatDate(r.rate_date)}</td><td>{r.source}</td></tr>)}
        </Table>
      )}
      <div className="mt-4 flex flex-wrap items-end gap-2">
        <Field label="Währung" className="w-28"><Select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>{data.currencies.filter((c) => c !== "EUR").map((c) => <option key={c}>{c}</option>)}</Select></Field>
        <Field label="Kurs heute (je 1 EUR)" className="w-44"><Input inputMode="decimal" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} /></Field>
        <Button disabled={!form.rate} onClick={async () => { try { await api("PUT", `/api/currencies/${form.currency}`, { rate_date: new Date().toISOString().slice(0, 10), rate: form.rate.replace(",", ".") }); toast("Kurs gespeichert."); setForm({ ...form, rate: "" }); load(); } catch (e) { toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"); } }}>Manuell eintragen</Button>
      </div>
    </Card>
  );
}
