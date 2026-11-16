"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Card } from "@/components/ui";

export function LogoUpload() {
  const [version, setVersion] = useState(0);
  const [hasLogo, setHasLogo] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/company-profile/logo", { cache: "no-store" })
      .then((r) => setHasLogo(r.ok))
      .catch(() => setHasLogo(false));
  }, [version]);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/company-profile/logo", { method: "POST", body: form });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `Fehler ${res.status}`);
      }
      setVersion((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    await fetch("/api/company-profile/logo", { method: "DELETE" });
    setBusy(false);
    setVersion((v) => v + 1);
  }

  return (
    <Card className="mb-4">
      <h2 className="mb-1 font-medium">Logo</h2>
      <p className="mb-3 text-sm text-slate-500">Erscheint oben rechts auf allen neuen Rechnungs-PDFs. SVG (empfohlen), PNG oder JPEG, max. 1 MB.</p>
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex h-16 w-48 items-center justify-center rounded-md border border-dashed border-slate-300 bg-white p-2 dark:border-slate-700">
          {hasLogo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={`/api/company-profile/logo?v=${version}`} alt="Firmenlogo" className="max-h-12 max-w-full" />
          ) : (
            <span className="text-xs text-slate-400">{hasLogo === null ? "…" : "kein Logo"}</span>
          )}
        </div>
        <label className="cursor-pointer inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800">
          {busy ? "Lade …" : "Logo hochladen"}
          <input
            type="file"
            accept="image/svg+xml,image/png,image/jpeg"
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
              e.target.value = "";
            }}
          />
        </label>
        {hasLogo && <Button onClick={remove} disabled={busy}>Entfernen</Button>}
      </div>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
    </Card>
  );
}
