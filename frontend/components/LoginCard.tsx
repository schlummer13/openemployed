"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getAuthConfig, type AuthConfig } from "@/lib/api";

const ERRORS: Record<string, string> = {
  cancelled: "Die Anmeldung wurde abgebrochen.",
  expired: "Die Anmeldung ist abgelaufen. Bitte erneut versuchen.",
  wrong_domain: "Dieses Konto gehört nicht zur Firmendomain.",
  provider_unavailable: "Der Anmeldedienst ist gerade nicht erreichbar.",
  no_email: "Der Anmeldedienst liefert keine E-Mail-Adresse.",
  email_unverified: "Die E-Mail-Adresse ist beim Anbieter nicht bestätigt.",
  not_allowed: "Dieses Konto ist für OpenEmployed nicht freigegeben.",
  disabled: "Dieses Konto ist gesperrt.",
  email_not_verified: "Die E-Mail-Adresse des Google-Kontos ist nicht bestätigt.",
  invalid_token: "Die Antwort von Google war ungültig. Bitte erneut versuchen.",
  token_exchange_failed: "Google hat die Anmeldung nicht bestätigt. Bitte erneut versuchen.",
};

function DevLogin({ next, domain }: { next: string; domain: string | null }) {
  const [email, setEmail] = useState(domain ? `test@${domain}` : "test@beispiel.de");
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    const res = await fetch("/api/auth/dev-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, name: "Entwickler" }),
    });
    if (res.ok) window.location.replace(next);
    else setErr(((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "Fehlgeschlagen");
  }
  return (
    <form onSubmit={submit} className="mt-6 space-y-2 rounded-lg border border-dashed border-amber-400 p-3">
      <p className="text-xs font-medium text-amber-700 dark:text-amber-300">Entwickler-Anmeldung (nur lokal, ohne Google)</p>
      <input
        aria-label="E-Mail für Entwickler-Anmeldung"
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
      />
      {err && <p className="text-xs text-red-600">{err}</p>}
      <button type="submit" className="w-full rounded-md bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600">
        Als Entwickler anmelden
      </button>
    </form>
  );
}

function PasswordLogin({ next }: { next: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      const res = await fetch("/api/auth/password/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, totp: needCode ? code : null }) });
      const body = (await res.json().catch(() => ({}))) as { status?: string; detail?: string };
      if (!res.ok) { setErr(body.detail ?? "Anmeldung fehlgeschlagen"); return; }
      if (body.status === "totp_required") { setNeedCode(true); return; }
      window.location.replace(body.status === "totp_setup" ? "/einstellungen/konto?2fa=1" : next);
    } finally { setBusy(false); }
  }
  const input = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950";
  return (
    <form onSubmit={submit} className="space-y-2.5">
      {!needCode ? <>
        <input aria-label="E-Mail" type="email" required autoComplete="username" placeholder="E-Mail" value={email} onChange={(e) => setEmail(e.target.value)} className={input} />
        <input aria-label="Passwort" type="password" required autoComplete="current-password" placeholder="Passwort" value={password} onChange={(e) => setPassword(e.target.value)} className={input} />
      </> : (
        <input aria-label="Zwei-Faktor-Code" inputMode="numeric" autoComplete="one-time-code" autoFocus required placeholder="6-stelliger Code aus der Authenticator-App" value={code} onChange={(e) => setCode(e.target.value)} className={input} />
      )}
      {err && <p className="text-xs text-red-600">{err}</p>}
      <button type="submit" disabled={busy} className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60">{needCode ? "Bestätigen" : "Anmelden"}</button>
    </form>
  );
}

const sso = "flex w-full items-center justify-center gap-3 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-800 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:hover:bg-slate-800";

function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function LoginCard() {
  const params = useSearchParams();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const error = params.get("error");
  const next = safeNext(params.get("next"));

  useEffect(() => {
    getAuthConfig().then(setConfig).catch(() => setConfig({ google_configured: false, domain: null }));
  }, []);

  return (
    <div className="w-full rounded-2xl border border-slate-200/80 bg-white/90 p-7 shadow-pop backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
      <h2 className="font-semibold">Anmelden</h2>
      <p className="mt-1 text-sm text-slate-500">
        Mit eurem Firmenkonto anmelden{config?.domain ? ` (@${config.domain})` : ""}.
      </p>

      {error && (
        <p role="alert" className="mt-5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {ERRORS[error] ?? "Anmeldung fehlgeschlagen."}
        </p>
      )}

      {config?.dev_login && <DevLogin next={next} domain={config.domain} />}

      {config && !config.google_configured && !config.microsoft_configured && !config.oidc_configured && !config.password_enabled && !config.dev_login && (
        <p className="mt-6 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          Noch kein Anmeldeverfahren eingerichtet – Google, Microsoft, OIDC oder E-Mail/Passwort in der .env aktivieren (docs/betrieb.md).
        </p>
      )}
      <div className="mt-6 space-y-2.5">
        {config?.google_configured && (
          <a href={`/api/auth/google/login?next=${encodeURIComponent(next)}`} className={sso}>
            <svg aria-hidden width="18" height="18" viewBox="0 0 48 48">
              <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z" />
              <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z" />
              <path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z" />
              <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.2-8.5 2.2-6.2 0-11.5-4.2-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
            </svg>
            Mit Google anmelden
          </a>
        )}
        {config?.microsoft_configured && (
          <a href={`/api/auth/microsoft/login?next=${encodeURIComponent(next)}`} className={sso}>
            <svg aria-hidden width="16" height="16" viewBox="0 0 21 21"><path fill="#f25022" d="M0 0h10v10H0z" /><path fill="#7fba00" d="M11 0h10v10H11z" /><path fill="#00a4ef" d="M0 11h10v10H0z" /><path fill="#ffb900" d="M11 11h10v10H11z" /></svg>
            Mit Microsoft anmelden
          </a>
        )}
        {config?.oidc_configured && <a href={`/api/auth/oidc/login?next=${encodeURIComponent(next)}`} className={sso}>{config.oidc_label || "Single Sign-on"}</a>}
        {config?.password_enabled && (config.google_configured || config.microsoft_configured || config.oidc_configured) && (
          <p className="flex items-center gap-3 py-1 text-xs text-slate-400"><span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />oder<span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" /></p>
        )}
        {config?.password_enabled && <PasswordLogin next={next} />}
      </div>
    </div>
  );
}
