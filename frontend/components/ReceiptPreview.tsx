"use client";

import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { Skeleton } from "./ui";

/** Beleg zum Abgleichen neben dem Formular: PDF-Seiten als Bild (serverseitig gerendert) oder das Foto selbst. */
export function ReceiptPreview({ fileId }: { fileId: string | null }) {
  const [state, setState] = useState<{ pages: number } | { error: string } | null>(null);
  const [zoom, setZoom] = useState(false);
  useEffect(() => {
    if (!fileId) { setState(null); return; }
    let alive = true;
    setState(null);
    void fetch(`/api/receipt-files/${fileId}/preview?page=1`, { credentials: "same-origin" }).then(async (r) => {
      if (!alive) return;
      if (r.ok) setState({ pages: Number(r.headers.get("X-Page-Count") ?? "1") || 1 });
      else setState({ error: ((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? "Keine Vorschau verfügbar" });
    }).catch(() => alive && setState({ error: "Vorschau konnte nicht geladen werden" }));
    return () => { alive = false; };
  }, [fileId]);

  if (!fileId) {
    return (
      <div className="grid h-full min-h-64 place-items-center rounded-xl border-2 border-dashed border-slate-200 p-6 text-center text-sm text-slate-500 dark:border-white/10">
        <div><Icon name="receipt" className="mx-auto mb-2 size-6 text-slate-400" />Noch keine Belegdatei.<br />Rechts „Datei wählen“ – ohne Beleg keine Buchung.</div>
      </div>
    );
  }
  if (state === null) return <Skeleton className="h-[70vh] rounded-xl" />;
  if ("error" in state) {
    return (
      <div className="flex h-full min-h-48 flex-col items-center justify-center gap-2 rounded-xl bg-slate-50 p-6 text-center text-sm text-slate-600 dark:bg-white/[0.03] dark:text-slate-300">
        <Icon name="file" className="size-6 text-slate-400" />
        <p>{state.error}</p>
        <a className="font-medium text-brand-700 hover:underline dark:text-brand-300" href={`/api/receipt-files/${fileId}`} target="_blank" rel="noopener">Datei öffnen</a>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>{state.pages > 1 ? `${state.pages} Seiten` : "1 Seite"}</span>
        <span className="flex gap-3">
          <button type="button" className="font-medium hover:text-slate-900 dark:hover:text-white" onClick={() => setZoom((z) => !z)}>{zoom ? "Einpassen" : "Vergrößern"}</button>
          <a className="font-medium hover:text-slate-900 dark:hover:text-white" href={`/api/receipt-files/${fileId}`} target="_blank" rel="noopener">Original öffnen</a>
        </span>
      </div>
      <div className={`space-y-3 overflow-auto rounded-xl bg-slate-100 p-3 dark:bg-white/[0.04] ${zoom ? "max-h-[75vh]" : ""}`}>
        {Array.from({ length: state.pages }, (_, i) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={i} src={`/api/receipt-files/${fileId}/preview?page=${i + 1}`} alt={`Beleg Seite ${i + 1}`}
            className={`mx-auto rounded-md bg-white shadow-card ${zoom ? "max-w-none w-[160%]" : "w-full"}`} />
        ))}
      </div>
    </div>
  );
}
