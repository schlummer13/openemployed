"use client";

import { useRef, useState } from "react";
import { Alert, Badge, Button, Drawer, Field, Textarea } from "@/components/ui";

export interface ChannelInfo {
  level: "ok" | "warn" | "block"; message: string; documenso: boolean; mail: boolean;
  qes: { configured: boolean; label: string };
}
export type Channel = "documenso" | "qes" | "paper" | "email";

/** Auswahl des Wegs zur Unterschrift – je nach Formvorschrift: Documenso, qualifiziert (externer Anbieter), Ausdruck, E-Mail. */
export function ChannelChooser({ open, onClose, info, title, onChoose, busy }: {
  open: boolean; onClose: () => void; info: ChannelInfo | null; title: string; busy?: boolean;
  onChoose: (channel: Channel, note: string | null) => void;
}) {
  const [note, setNote] = useState("");
  if (!info) return null;
  const options: { key: Channel; label: string; text: string; available: boolean; why?: string; recommended?: boolean }[] = [
    { key: "documenso", label: "Digital über Documenso", recommended: info.level === "ok",
      text: info.level === "ok" ? "Einfach und schnell – für diesen Vertrag rechtlich ausreichend." : "Nur als zusätzliche digitale Kopie – ersetzt die Schriftform nicht.",
      available: info.documenso && info.level !== "block", why: !info.documenso ? "Documenso nicht verbunden" : info.level === "block" ? "elektronische Form ausgeschlossen" : undefined },
    { key: "qes", label: `Qualifiziert elektronisch${info.qes.configured ? ` (${info.qes.label})` : ""}`, recommended: info.level === "warn" && info.qes.configured,
      text: "Ersetzt die Schriftform (§ 126a BGB). Die unterschreibende Person identifiziert sich einmalig beim Anbieter.",
      available: info.qes.configured && info.level !== "block",
      why: info.level === "block" ? "auch qualifiziert nicht zulässig" : !info.qes.configured ? "kein Anbieter eingerichtet (System & Integrationen → Qualifizierte Signatur)" : undefined },
    { key: "paper", label: "Ausdrucken und unterschreiben", recommended: info.level === "block" || (info.level === "warn" && !info.qes.configured),
      text: "PDF wird geöffnet. Nach der Unterschrift den Scan hier hochladen – das Original aufbewahren.", available: true },
    { key: "email", label: "Per E-Mail zum Ausdrucken senden",
      text: info.level === "block" ? "Nur Vorab-Information – wirksam wird erst das zugegangene Original." : "PDF als Anhang mit der Bitte, auszudrucken, zu unterschreiben und das Original zurückzusenden.",
      available: info.mail, why: !info.mail ? "E-Mail-Versand nicht eingerichtet" : undefined },
  ];
  return (
    <Drawer open={open} onClose={onClose} title="Unterschrift einholen" subtitle={title}>
      <div className="space-y-4">
        <Alert kind={info.level === "ok" ? "success" : info.level === "warn" ? "warning" : "error"}>{info.level === "ok" ? `Digital wirksam – ${info.message}` : info.message}</Alert>
        <div className="space-y-2">
          {options.map((o) => (
            <button key={o.key} type="button" disabled={!o.available || busy} onClick={() => onChoose(o.key, note.trim() || null)}
              className="flex w-full items-start gap-3 rounded-xl border border-slate-200 p-3 text-left transition hover:border-brand-500 hover:bg-brand-50/40 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:hover:bg-white/[0.03]">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 font-medium">{o.label}{o.recommended && o.available && <Badge tone="green">empfohlen</Badge>}</p>
                <p className="mt-0.5 text-xs text-slate-500">{o.why ? `Nicht verfügbar: ${o.why}` : o.text}</p>
              </div>
            </button>
          ))}
        </div>
        <Field label="Hinweis für E-Mail bzw. Ablage (optional)"><Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="z. B. Bitte bis 30.11. zurücksenden" /></Field>
      </div>
    </Drawer>
  );
}

/** Kleiner Knopf zum Hochladen des eingescannten, unterschriebenen Exemplars. */
export function SignedCopyButton({ onUpload, label = "Unterschriebenen Scan hochladen" }: { onUpload: (file: File) => void; label?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }} />
      <Button className="!px-2 !py-1 text-xs" onClick={() => ref.current?.click()}>{label}</Button>
    </>
  );
}
