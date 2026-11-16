"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { Badge, Card } from "@/components/ui";
import { formatEuro } from "@/lib/api";
import { api, HttpError } from "@/lib/http";

interface Capture {
  key: string; name: string; preview: string | null; state: "upload" | "read" | "done" | "error"; message?: string;
  receipt?: { supplier_name: string; gross_amount: string; description: string; ready: boolean; blockers: string[] };
}

/** Foto verkleinern (max. 2000 px, JPEG) – schneller Upload, ausreichend für die Erkennung. */
async function shrink(file: File): Promise<Blob> {
  if (!file.type.startsWith("image/")) return file;
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) return file;
  const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return await new Promise<Blob>((resolve) => canvas.toBlob((b) => resolve(b ?? file), "image/jpeg", 0.85));
}

export default function CapturePage() {
  const [items, setItems] = useState<Capture[]>([]);
  const camera = useRef<HTMLInputElement>(null);
  const files = useRef<HTMLInputElement>(null);
  const update = (key: string, patch: Partial<Capture>) => setItems((list) => list.map((i) => (i.key === key ? { ...i, ...patch } : i)));

  async function handle(list: FileList | null) {
    for (const file of Array.from(list ?? [])) {
      const key = `${Date.now()}-${file.name}-${Math.random()}`;
      const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
      setItems((l) => [{ key, name: file.name, preview, state: "upload" }, ...l]);
      try {
        const blob = await shrink(file);
        const fd = new FormData();
        fd.append("file", blob, blob === file ? file.name : file.name.replace(/\.\w+$/, "") + ".jpg");
        const res = await fetch("/api/receipt-files", { method: "POST", body: fd });
        const up = (await res.json().catch(() => ({}))) as { id?: string; detail?: string; duplicate_of_receipt?: string | null };
        if (!res.ok || !up.id) throw new Error(up.detail ?? `Fehler ${res.status}`);
        if (up.duplicate_of_receipt) { update(key, { state: "error", message: "Dieser Beleg wurde bereits erfasst." }); continue; }
        update(key, { state: "read" });
        const r = await api<{ draft: boolean; notes: string[]; receipt: Capture["receipt"] | null }>("POST", `/api/receipt-files/${up.id}/capture`);
        update(key, r.draft && r.receipt ? { state: "done", receipt: r.receipt } : { state: "error", message: r.notes.join(" ") || "Nicht erkannt – bitte am Rechner erfassen." });
      } catch (e) {
        update(key, { state: "error", message: e instanceof HttpError ? e.message : e instanceof Error ? e.message : "Upload fehlgeschlagen" });
      }
    }
  }

  return (
    <div className="mx-auto max-w-lg">
      <h1 className="text-[26px] font-semibold tracking-tight">Beleg erfassen</h1>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Foto machen – die Daten werden ausgelesen und der Beleg nach der Regel des Lieferanten vorbereitet. Gebucht wird später per Klick.</p>

      <div className="mt-6 grid gap-3">
        <button type="button" onClick={() => camera.current?.click()}
          className="flex h-36 flex-col items-center justify-center gap-2 rounded-2xl bg-brand-600 text-white shadow-raised transition active:scale-[0.99]">
          <Icon name="camera" className="size-9" />
          <span className="text-base font-semibold">Foto aufnehmen</span>
        </button>
        <button type="button" onClick={() => files.current?.click()}
          className="flex h-14 items-center justify-center gap-2 rounded-xl bg-white text-sm font-medium shadow-card dark:bg-slate-900 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.08)]">
          <Icon name="file" />PDF oder Bild auswählen
        </button>
        <input ref={camera} type="file" accept="image/*" capture="environment" className="sr-only" aria-label="Foto aufnehmen" onChange={(e) => { void handle(e.target.files); e.target.value = ""; }} />
        <input ref={files} type="file" multiple accept="image/*,application/pdf,.xml" className="sr-only" aria-label="Datei auswählen" onChange={(e) => { void handle(e.target.files); e.target.value = ""; }} />
      </div>

      {items.length > 0 && (
        <div className="mt-8 space-y-3">
          <h2 className="text-sm font-semibold text-slate-500">Erfasst</h2>
          {items.map((i) => (
            <Card key={i.key} className="!p-3">
              <div className="flex gap-3">
                <div className="grid size-16 shrink-0 place-items-center overflow-hidden rounded-lg bg-slate-100 dark:bg-white/5">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {i.preview ? <img src={i.preview} alt="" className="size-full object-cover" /> : <Icon name="file" className="size-6 text-slate-400" />}
                </div>
                <div className="min-w-0 flex-1 text-sm">
                  {i.state === "upload" && <p className="flex items-center gap-2 text-slate-500"><Spinner />Wird hochgeladen …</p>}
                  {i.state === "read" && <p className="flex items-center gap-2 text-slate-500"><Spinner />Beleg wird gelesen … <span className="text-xs">(bis zu 1 Min.)</span></p>}
                  {i.state === "done" && i.receipt && (
                    <>
                      <p className="truncate font-medium">{i.receipt.supplier_name}</p>
                      <p className="truncate text-xs text-slate-500">{i.receipt.description}</p>
                      <p className="mt-1 flex flex-wrap items-center gap-2"><span className="tabular font-semibold">{formatEuro(i.receipt.gross_amount)}</span>
                        {i.receipt.ready ? <Badge tone="green">bereit zum Buchen</Badge> : <Badge tone="amber">{i.receipt.blockers[0] ?? "bitte prüfen"}</Badge>}</p>
                    </>
                  )}
                  {i.state === "error" && <p className="text-amber-700 dark:text-amber-300">{i.message}</p>}
                </div>
              </div>
            </Card>
          ))}
          <Link href="/belege" className="block py-2 text-center text-sm font-medium text-brand-700 dark:text-brand-300">Zu den Belegen →</Link>
        </div>
      )}
    </div>
  );
}

function Spinner() {
  return <span className="size-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" aria-hidden="true" />;
}
