"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useUser } from "@/components/AuthGate";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, CardHeader, Field, Input, PageHeader } from "@/components/ui";
import { getMe, type Me } from "@/lib/api";
import { api, HttpError } from "@/lib/http";

export default function AccountPage() {
  const initial = useUser();
  const [me, setMe] = useState<Me | null>(initial);
  const [pw, setPw] = useState({ current: "", next: "", repeat: "" });
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const { toast } = useFeedback();
  const reload = () => void getMe().then(setMe);
  useEffect(() => { if (new URLSearchParams(window.location.search).get("2fa")) void start(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const savePw = async (e: FormEvent) => {
    e.preventDefault();
    if (pw.next !== pw.repeat) { toast("Die neuen Passwörter stimmen nicht überein.", "error"); return; }
    try { await api("POST", "/api/auth/password", { current: pw.current || null, new: pw.next }); toast("Passwort gespeichert."); setPw({ current: "", next: "", repeat: "" }); reload(); }
    catch (err) { toast(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen", "error"); }
  };
  async function start() {
    try { setSetup(await api<{ secret: string; qr: string }>("POST", "/api/auth/totp/start")); } catch (err) { toast(err instanceof HttpError ? err.message : "Fehlgeschlagen", "error"); }
  }
  const confirm2fa = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("POST", "/api/auth/totp/confirm", { code }); toast("Zwei-Faktor-Anmeldung aktiv."); setSetup(null); setCode(""); reload(); }
    catch (err) { toast(err instanceof HttpError ? err.message : "Code ungültig", "error"); }
  };
  if (!me) return null;
  return (
    <>
      <PageHeader title="Mein Konto" eyebrow="System" description={`${me.email} · ${me.role_label ?? me.role}`} />
      {me.needs_2fa && <div className="mb-4"><Alert kind="warning">Für euer Unternehmen ist die Zwei-Faktor-Anmeldung Pflicht – bitte jetzt einrichten.</Alert></div>}
      <div className="grid max-w-4xl gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Passwort" description={me.has_password ? "Für die Anmeldung mit E-Mail und Passwort (mind. 12 Zeichen)." : "Optional – zusätzlich zur Anmeldung per Google/Microsoft."} />
          <form onSubmit={savePw} className="grid gap-3">
            {me.has_password && <Field label="Aktuelles Passwort"><Input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>}
            <Field label="Neues Passwort"><Input type="password" autoComplete="new-password" minLength={12} required value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
            <Field label="Wiederholen"><Input type="password" autoComplete="new-password" required value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} /></Field>
            <div><Button type="submit" variant="primary">Speichern</Button></div>
          </form>
        </Card>
        <Card>
          <CardHeader title="Zwei-Faktor-Anmeldung" description="Code aus einer Authenticator-App (z. B. Google Authenticator, Microsoft Authenticator, 1Password) bei der Passwort-Anmeldung." />
          {me.totp_enabled && !setup ? <div className="flex flex-wrap items-center gap-3"><Badge tone="green">aktiv</Badge><Button onClick={() => void start()}>Neues Gerät einrichten</Button></div>
            : setup ? (
              <form onSubmit={confirm2fa} className="grid gap-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={setup.qr} alt="QR-Code für die Authenticator-App" className="size-44 rounded-lg bg-white p-2" />
                <p className="break-all text-xs text-slate-500">Manuell: <code>{setup.secret}</code></p>
                <Field label="Code aus der App"><Input inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} /></Field>
                <div><Button type="submit" variant="primary">Aktivieren</Button></div>
              </form>
            ) : <Button variant="primary" onClick={() => void start()}>Einrichten</Button>}
        </Card>
      </div>
    </>
  );
}
