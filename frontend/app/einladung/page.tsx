"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";

interface Info { email: string; name: string | null; role_label: string; google: boolean; microsoft: boolean; oidc: boolean }

function Accept() {
  const token = useSearchParams().get("token") ?? "";
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pw, setPw] = useState({ a: "", b: "" });
  useEffect(() => {
    void fetch(`/api/auth/invite/${encodeURIComponent(token)}`).then(async (r) => (r.ok ? setInfo(await r.json()) : setError("Einladung ungültig oder abgelaufen.")));
  }, [token]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (pw.a !== pw.b) { setError("Die Passwörter stimmen nicht überein."); return; }
    const r = await fetch(`/api/auth/invite/${encodeURIComponent(token)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw.a }) });
    if (r.ok) window.location.replace("/");
    else setError(((await r.json().catch(() => ({}))) as { detail?: string | { msg: string }[] }).detail?.toString() ?? "Fehlgeschlagen");
  };
  const input = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950";
  return (
    <div className="w-full max-w-sm rounded-2xl border border-slate-200/80 bg-white/90 p-7 shadow-pop dark:border-slate-800 dark:bg-slate-900/90">
      <h1 className="text-lg font-semibold">Zugang einrichten</h1>
      {error && <p role="alert" className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{error}</p>}
      {info && (
        <>
          <p className="mt-1 text-sm text-slate-500">{info.email} · {info.role_label}</p>
          {(info.google || info.microsoft || info.oidc) && <p className="mt-4 text-sm">Ihr könnt euch direkt mit eurem Firmenkonto anmelden ({[info.google && "Google", info.microsoft && "Microsoft", info.oidc && "SSO"].filter(Boolean).join(", ")}) – <a className="font-medium text-brand-600 hover:underline" href="/login">zur Anmeldung</a>. Oder ein Passwort festlegen:</p>}
          <form onSubmit={submit} className="mt-4 space-y-2.5">
            <input aria-label="Passwort" type="password" autoComplete="new-password" minLength={12} required placeholder="Passwort (mind. 12 Zeichen)" value={pw.a} onChange={(e) => setPw({ ...pw, a: e.target.value })} className={input} />
            <input aria-label="Passwort wiederholen" type="password" autoComplete="new-password" required placeholder="Wiederholen" value={pw.b} onChange={(e) => setPw({ ...pw, b: e.target.value })} className={input} />
            <button type="submit" className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-700">Passwort festlegen</button>
          </form>
        </>
      )}
    </div>
  );
}

export default function InvitePage() {
  return <div className="grid min-h-screen place-items-center px-4"><Suspense><Accept /></Suspense></div>;
}
