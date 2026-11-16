"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useUser } from "@/components/AuthGate";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, Drawer, Field, Input, PageHeader, PageSkeleton, Select, Table, Tabs, Textarea } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface U { id: string; email: string; name: string | null; role: string; active: boolean; auth_provider: string; totp_enabled: boolean; has_password: boolean; last_login_at: string | null; invite_pending: boolean; locked: boolean }
const PROVIDER: Record<string, string> = { google: "Google", microsoft: "Microsoft", oidc: "SSO", password: "Passwort", dev: "Entwickler" };
const HINT: Record<string, string> = {
  admin: "alles, inkl. Benutzer, Einstellungen und Integrationen",
  accounting: "alle Fachfunktionen (Rechnungen, Belege, Buchhaltung, Lohn) – keine Verwaltung",
  advisor: "nur lesen, DATEV-/GoBD-Export – ideal für die Steuerkanzlei",
  employee: "Projekte, Aufgaben, Zeiterfassung, Belegfoto – keine Finanzen",
};

function UsersTab() {
  const me = useUser();
  const [data, setData] = useState<{ users: U[]; roles: Record<string, string> } | null>(null);
  const [form, setForm] = useState<{ email: string; name: string; role: string } | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<{ users: U[]; roles: Record<string, string> }>("GET", "/api/admin/users").then(setData); }, []);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); toast(msg); load(); } catch (e) { toast(e instanceof HttpError ? e.message : "Fehlgeschlagen", "error"); } };
  const invite = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    try {
      const r = await api<{ mailed: boolean; link: string | null }>("POST", "/api/admin/users", { email: form.email, name: form.name || null, role: form.role });
      toast(r.mailed ? "Einladung per E-Mail versendet." : "Benutzer angelegt – Link bitte selbst weitergeben.");
      setLink(r.link); setForm(null); load();
    } catch (err) { toast(err instanceof HttpError ? err.message : "Einladen fehlgeschlagen", "error"); }
  };
  if (!data) return <PageSkeleton stats={0} />;
  return (
    <>
      <div className="mb-4 flex justify-end"><Button variant="primary" onClick={() => setForm({ email: "", name: "", role: "accounting" })}>+ Einladen</Button></div>
      {link && <div className="mb-4"><Alert kind="info">E-Mail ist nicht eingerichtet – Einladungslink (7 Tage gültig): <code className="break-all">{link}</code></Alert></div>}
      <Card>
        <Table head={<tr><th>Benutzer</th><th>Rolle</th><th>Anmeldung</th><th>Zuletzt</th><th>Status</th><th /></tr>}>
          {data.users.map((u) => (
            <tr key={u.id}>
              <td><span className="font-medium">{u.name ?? u.email}</span><span className="block text-xs text-slate-500">{u.email}</span></td>
              <td>
                <Select aria-label={`Rolle von ${u.email}`} value={u.role} disabled={u.id === me?.id} className="!w-52"
                  onChange={(e) => void act(() => api("PUT", `/api/admin/users/${u.id}`, { role: e.target.value, active: u.active }), "Rolle geändert.")}>
                  {Object.entries(data.roles).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </Select>
              </td>
              <td className="text-xs">{PROVIDER[u.auth_provider] ?? u.auth_provider}{u.totp_enabled && <Badge tone="green">2FA</Badge>}</td>
              <td className="text-xs tabular">{u.last_login_at ? formatDate(u.last_login_at) : "–"}</td>
              <td>{!u.active ? <Badge tone="red">gesperrt</Badge> : u.invite_pending ? <Badge tone="amber">eingeladen</Badge> : u.locked ? <Badge tone="amber">vorübergehend gesperrt</Badge> : <Badge tone="green">aktiv</Badge>}</td>
              <td className="whitespace-nowrap text-right">
                {u.totp_enabled && <Button variant="ghost" className="!h-7 !text-xs" onClick={async () => { if (await confirm(`2FA von ${u.email} zurücksetzen?\n\nDie Person muss die Zwei-Faktor-Anmeldung neu einrichten.`)) void act(() => api("POST", `/api/admin/users/${u.id}/reset-2fa`), "2FA zurückgesetzt."); }}>2FA zurücksetzen</Button>}
                {u.id !== me?.id && <Button variant="ghost" className="!h-7 !text-xs" onClick={async () => {
                  if (u.active && !(await confirm(`${u.email} sperren?\n\nAlle Sitzungen enden sofort. Der Benutzer bleibt für das Protokoll erhalten.`))) return;
                  void act(() => api("PUT", `/api/admin/users/${u.id}`, { role: u.role, active: !u.active }), u.active ? "Benutzer gesperrt." : "Benutzer entsperrt.");
                }}>{u.active ? "Sperren" : "Entsperren"}</Button>}
              </td>
            </tr>
          ))}
        </Table>
      </Card>

      <Drawer open={!!form} onClose={() => setForm(null)} title="Benutzer einladen" subtitle="Die Person erhält einen Link zum Einrichten des Zugangs"
        footer={<><Button onClick={() => setForm(null)}>Abbrechen</Button><Button type="submit" form="invite-form" variant="primary">Einladen</Button></>}>
        {form && (
          <form id="invite-form" onSubmit={invite} className="grid gap-4">
            <Field label="E-Mail"><Input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Name (optional)"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Rolle" hint={HINT[form.role] ?? "eigene Rolle"}><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{Object.entries(data.roles).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
          </form>
        )}
      </Drawer>
    </>
  );
}

export default function UsersPage() {
  const [tab, setTab] = useState<"users" | "roles">("users");
  return (
    <>
      <PageHeader title="Benutzer & Rollen" eyebrow="System" description="Wer darf was? Rollen vergeben je Bereich „kein Zugriff“, „lesen“ oder „bearbeiten“ – die Navigation zeigt nur freigegebene Bereiche." />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[{ value: "users", label: "Benutzer" }, { value: "roles", label: "Rollen & Rechte" }]} /></div>
      {tab === "users" ? <UsersTab /> : <RolesTab />}
    </>
  );
}

interface RoleRow { key: string; label: string; description: string | null; builtin: boolean; editable: boolean; customized: boolean; permissions: Record<string, string>; users: number }
interface Area { key: string; label: string; description: string }
const LEVELS: [string, string][] = [["none", "–"], ["read", "lesen"], ["write", "bearbeiten"]];

function RolesTab() {
  const [data, setData] = useState<{ roles: RoleRow[]; areas: Area[] } | null>(null);
  const [edit, setEdit] = useState<{ key: string; label: string; description: string; permissions: Record<string, string>; isNew: boolean } | null>(null);
  const { toast, confirm } = useFeedback();
  const load = useCallback(() => { void api<{ roles: RoleRow[]; areas: Area[] }>("GET", "/api/admin/roles").then(setData); }, []);
  useEffect(load, [load]);
  if (!data) return <PageSkeleton stats={0} />;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    try { await api("PUT", `/api/admin/roles/${edit.key}`, { label: edit.label, description: edit.description || null, permissions: edit.permissions }); toast("Rolle gespeichert – gilt sofort."); setEdit(null); load(); }
    catch (err) { toast(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen", "error"); }
  };
  const cell = (lvl: string) => lvl === "write" ? <span className="inline-block rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300">bearbeiten</span>
    : lvl === "read" ? <span className="inline-block rounded-full bg-sky-100 px-2 py-0.5 text-[11px] font-semibold text-sky-800 dark:bg-sky-500/15 dark:text-sky-300">lesen</span> : <span className="text-slate-300 dark:text-slate-600">–</span>;
  return (
    <>
      <div className="mb-4 flex justify-end"><Button variant="primary" onClick={() => setEdit({ key: "", label: "", description: "", permissions: Object.fromEntries(data.areas.map((a) => [a.key, "none"])), isNew: true })}>+ Neue Rolle</Button></div>
      <Card className="!p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="border-b border-slate-100 text-xs text-slate-500 dark:border-white/5">
              <tr><th className="px-4 py-3 text-left font-medium">Bereich</th>{data.roles.map((r) => (
                <th key={r.key} className="px-2 py-3 text-center font-medium">
                  <button type="button" disabled={!r.editable} onClick={() => setEdit({ key: r.key, label: r.label, description: r.description ?? "", permissions: { ...r.permissions }, isNew: false })} className="font-semibold text-slate-800 enabled:hover:text-brand-700 enabled:hover:underline dark:text-slate-100">{r.label}</button>
                  <span className="block text-[10px] font-normal">{r.users} Benutzer{r.builtin ? "" : " · eigene"}</span>
                </th>))}</tr>
            </thead>
            <tbody>
              {data.areas.map((a) => (
                <tr key={a.key} className="border-b border-slate-50 last:border-0 dark:border-white/5">
                  <td className="px-4 py-2.5"><span className="font-medium">{a.label}</span><span className="block text-xs text-slate-500">{a.description}</span></td>
                  {data.roles.map((r) => <td key={r.key} className="px-2 py-2.5 text-center">{cell(r.permissions[a.key] ?? "none")}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <p className="mt-3 text-xs text-slate-500">Eigene Daten (Konto, Stempeluhr, Urlaub, Handbuch) sind für alle erreichbar. Die Geschäftsführung hat immer alle Rechte. Spaltenkopf anklicken zum Bearbeiten.</p>
      <Drawer open={!!edit} onClose={() => setEdit(null)} title={edit?.isNew ? "Neue Rolle" : `Rolle: ${edit?.label}`} subtitle="Rechte je Bereich"
        footer={edit && <>
          {!edit.isNew && <Button variant="ghost" className="mr-auto" onClick={async () => { const r = data.roles.find((x) => x.key === edit.key); if (await confirm(r?.builtin ? `Standardrolle „${edit.label}“ zurücksetzen?` : `Rolle „${edit.label}“ löschen?`)) { try { await api("DELETE", `/api/admin/roles/${edit.key}`); toast(r?.builtin ? "Zurückgesetzt." : "Gelöscht."); setEdit(null); load(); } catch (err) { toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"); } } }}>{data.roles.find((x) => x.key === edit.key)?.builtin ? "Zurücksetzen" : "Löschen"}</Button>}
          <Button onClick={() => setEdit(null)}>Abbrechen</Button><Button type="submit" form="role-form" variant="primary">Speichern</Button>
        </>}>
        {edit && (
          <form id="role-form" onSubmit={save} className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Bezeichnung"><Input required value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value, key: edit.isNew ? e.target.value.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "r_$1").slice(0, 40) : edit.key })} /></Field>
              <Field label="Kennung" hint="technisch, nicht änderbar"><Input value={edit.key} disabled /></Field>
            </div>
            <Field label="Beschreibung (optional)"><Textarea rows={2} value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
            <div className="grid gap-2">
              {data.areas.map((a) => (
                <div key={a.key} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-100 px-3 py-2 dark:border-white/5">
                  <span className="min-w-0 flex-1 text-sm"><span className="font-medium">{a.label}</span><span className="block text-xs text-slate-500">{a.description}</span></span>
                  <span className="inline-flex rounded-lg bg-slate-100 p-0.5 dark:bg-white/5">
                    {LEVELS.map(([v, l]) => <button key={v} type="button" onClick={() => setEdit({ ...edit, permissions: { ...edit.permissions, [a.key]: v } })}
                      className={`rounded-md px-2.5 py-1 text-xs font-medium ${edit.permissions[a.key] === v ? "bg-white shadow-card dark:bg-slate-800" : "text-slate-500"}`}>{l}</button>)}
                  </span>
                </div>
              ))}
            </div>
          </form>
        )}
      </Drawer>
    </>
  );
}
