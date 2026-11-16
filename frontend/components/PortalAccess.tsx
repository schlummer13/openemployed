"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Input } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface Access { id: string; email: string; status: "active" | "disabled"; created_at: string; last_login_at: string | null }

export function PortalAccess({ customerId, defaultEmail }: { customerId: string; defaultEmail: string }) {
  const [items, setItems] = useState<Access[]>([]);
  const [email, setEmail] = useState(defaultEmail);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { void api<Access[]>("GET", `/api/customers/${customerId}/portal-access`).then(setItems); }, [customerId]);
  useEffect(load, [load]);
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof HttpError ? e.message : "Aktion fehlgeschlagen"); }
  }
  return (
    <Card className="mb-6">
      <h2 className="mb-1 font-medium">Kundenportal-Zugang</h2>
      <p className="mb-3 text-sm text-slate-500">Freigeschaltete Adressen melden sich mit einem Code per E-Mail an und sehen nur die Belege dieses Kunden.</p>
      {error && <div className="mb-2"><Alert>{error}</Alert></div>}
      <ul className="mb-3 space-y-1 text-sm">
        {items.map((a) => (
          <li key={a.id} className="flex items-center justify-between gap-2">
            <span className={a.status === "disabled" ? "text-slate-400 line-through" : ""}>{a.email} <span className="text-xs text-slate-500">{a.last_login_at ? `zuletzt angemeldet ${formatDate(a.last_login_at.slice(0, 10))}` : "noch nie angemeldet"}</span></span>
            {a.status === "active" && <Button className="!px-2 !h-7 !py-0 !text-xs" onClick={() => act(() => api("DELETE", `/api/portal-access/${a.id}`))}>Zugang sperren</Button>}
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Input aria-label="E-Mail für Portalzugang" type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="max-w-xs" />
        <Button onClick={() => act(() => api("POST", `/api/customers/${customerId}/portal-access`, { email }))} disabled={!email.includes("@")}>Zugang freischalten</Button>
        <Button variant="ghost" onClick={() => act(async () => { await api("POST", `/api/customers/${customerId}/welcome`); setError(null); window.alert("Willkommensmail versendet."); })}>Willkommensmail senden</Button>
      </div>
    </Card>
  );
}
