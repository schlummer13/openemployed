"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

/* ───────────────────────── Hinweise (Toasts) ───────────────────────── */

type ToastKind = "success" | "info" | "error";
interface Toast { id: number; kind: ToastKind; text: string }

/* ───────────────────────── Dialoge ───────────────────────── */

export interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  /** Eingabefeld (z. B. Storno-Grund); dann liefert ask() den Text statt true */
  input?: { label: string; placeholder?: string; minLength?: number; secret?: boolean };
}

interface DialogState extends ConfirmOptions { resolve: (v: string | boolean | null) => void }

interface FeedbackApi {
  toast: (text: string, kind?: ToastKind) => void;
  confirm: (opts: ConfirmOptions | string) => Promise<boolean>;
  prompt: (opts: ConfirmOptions | string) => Promise<string | null>;
}

const DANGER = /lösch|storn|entfern|ablehn|sperren|archivier/i;

/** Einfacher Text wie bei window.confirm: erste Zeile = Titel, Rest = Erläuterung; riskante Aktionen rot. */
function fromText(text: string, input: boolean): ConfirmOptions {
  const [title = "", ...rest] = text.split("\n\n");
  const message = rest.join("\n\n").trim();
  return {
    title: input ? "Bitte angeben" : title, tone: DANGER.test(text) ? "danger" : "primary",
    message: input ? undefined : message ? <span className="whitespace-pre-line">{message}</span> : undefined,
    input: input ? { label: title, minLength: 3 } : undefined,
  };
}

const Ctx = createContext<FeedbackApi | null>(null);

export function useFeedback(): FeedbackApi {
  const ctx = useContext(Ctx);
  if (ctx) return ctx;
  // Ohne Provider (z. B. öffentliche Seiten): einfache Browser-Fallbacks
  return {
    toast: () => undefined,
    confirm: async (o) => window.confirm(typeof o === "string" ? o : `${o.title}\n\n${typeof o.message === "string" ? o.message : ""}`),
    prompt: async (o) => window.prompt(typeof o === "string" ? o : o.input?.label ?? o.title),
  };
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const seq = useRef(0);

  const toast = useCallback((text: string, kind: ToastKind = "success") => {
    const id = ++seq.current;
    setToasts((t) => [...t.slice(-3), { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 9000 : 5500);
  }, []);
  const confirm = useCallback((o: ConfirmOptions | string) => new Promise<boolean>((resolve) => {
    const opts = typeof o === "string" ? fromText(o, false) : o;
    setDialog({ ...opts, resolve: (v) => resolve(v === true) });
  }), []);
  const prompt = useCallback((o: ConfirmOptions | string) => new Promise<string | null>((resolve) => {
    const opts = typeof o === "string" ? fromText(o, true) : o;
    setDialog({ ...opts, input: opts.input ?? { label: opts.title }, resolve: (v) => resolve(typeof v === "string" ? v : null) });
  }), []);

  return (
    <Ctx.Provider value={{ toast, confirm, prompt }}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-4 bottom-4 z-[70] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-6 sm:bottom-6">
        {toasts.map((t) => (
          <div key={t.id} role={t.kind === "error" ? "alert" : "status"}
            className="pointer-events-auto flex w-full max-w-sm animate-pop items-start gap-3 rounded-xl bg-slate-900 px-4 py-3 text-sm text-white shadow-pop dark:bg-white dark:text-slate-900">
            <span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full ${t.kind === "error" ? "bg-red-500" : t.kind === "info" ? "bg-brand-500" : "bg-emerald-500"} text-white`}>
              <Icon name={t.kind === "error" ? "x" : "check"} className="size-3" />
            </span>
            <span className="min-w-0 flex-1 leading-relaxed">{t.text}</span>
            <button type="button" aria-label="Hinweis schließen" className="shrink-0 opacity-60 hover:opacity-100" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
              <Icon name="x" className="size-4" />
            </button>
          </div>
        ))}
      </div>
      {dialog && <Dialog state={dialog} onClose={(v) => { dialog.resolve(v); setDialog(null); }} />}
    </Ctx.Provider>
  );
}

function Dialog({ state, onClose }: { state: DialogState; onClose: (v: string | boolean | null) => void }) {
  const [text, setText] = useState("");
  const ok = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    (state.input ? field.current : ok.current)?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(state.input ? null : false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, onClose]);
  const min = state.input?.minLength ?? 0;
  const valid = !state.input || text.trim().length >= Math.max(1, min);
  const submit = () => { if (valid) onClose(state.input ? text.trim() : true); };
  return (
    <div className="fixed inset-0 z-[80] grid place-items-center p-4" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title">
      <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px]" onClick={() => onClose(state.input ? null : false)} />
      <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="relative w-full max-w-md animate-pop rounded-2xl bg-white p-6 shadow-pop dark:bg-slate-900">
        <div className="flex gap-4">
          <span className={`grid size-10 shrink-0 place-items-center rounded-full ${state.tone === "danger" ? "bg-red-50 text-red-600 dark:bg-red-500/10" : "bg-brand-50 text-brand-600 dark:bg-brand-500/10 dark:text-brand-300"}`}>
            <Icon name={state.tone === "danger" ? "alarm" : "shield"} className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="dlg-title" className="text-base font-semibold leading-snug">{state.title}</h2>
            {state.message && <div className="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-slate-300">{state.message}</div>}
            {state.input && (
              <label className="mt-4 block text-sm">
                <span className="mb-1.5 block text-[13px] font-medium">{state.input.label}</span>
                <input ref={field} value={text} onChange={(e) => setText(e.target.value)} placeholder={state.input.placeholder}
                  type={state.input.secret ? "password" : "text"} autoComplete={state.input.secret ? "off" : undefined}
                  className="h-9 w-full rounded-lg border-0 bg-white px-3 text-sm shadow-[0_0_0_1px_rgb(15_15_25/0.12)] outline-none focus:shadow-[0_0_0_1px_var(--color-brand-500),0_0_0_4px_rgb(18_160_147/0.15)] dark:bg-slate-850 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.1)]" />
                {min > 1 && <span className="mt-1 block text-xs text-slate-500">mindestens {min} Zeichen</span>}
              </label>
            )}
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => onClose(state.input ? null : false)}
            className="inline-flex h-9 items-center rounded-lg bg-white px-3.5 text-sm font-medium shadow-[0_0_0_1px_rgb(15_15_25/0.12)] hover:bg-slate-50 dark:bg-slate-850 dark:shadow-[0_0_0_1px_rgb(255_255_255/0.1)] dark:hover:bg-slate-800">
            {state.cancelLabel ?? "Abbrechen"}
          </button>
          <button ref={ok} type="submit" disabled={!valid}
            className={`inline-flex h-9 items-center rounded-lg px-3.5 text-sm font-medium text-white disabled:opacity-50 ${state.tone === "danger" ? "bg-red-600 hover:bg-red-700" : "bg-brand-600 hover:bg-brand-700"}`}>
            {state.confirmLabel ?? (state.input ? "Übernehmen" : state.tone === "danger" ? "Ja, fortfahren" : "Bestätigen")}
          </button>
        </div>
      </form>
    </div>
  );
}
