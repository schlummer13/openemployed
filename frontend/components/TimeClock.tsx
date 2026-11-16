"use client";

import { useCallback, useEffect, useState } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Card, CardHeader, Empty, Skeleton } from "@/components/ui";
import { HttpError } from "@/lib/http";

type Fetcher = <T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown) => Promise<T>;

interface ClockEntry { id: string; started_at: string; ended_at: string | null; break_minutes: number; worked_minutes: number | null; source: string; note: string | null }
interface ClockStatus {
  employee: { id: string; name: string; weekly_hours: string | null; work_days: number[] };
  open: { id: string; started_at: string } | null;
  server_time: string;
  minor: boolean;
  today: { presence: number; break: number; net: number };
  week: { net: number; target: number; diff: number | null };
  days: { date: string; net: number; entries: ClockEntry[] }[];
  warning: string | null;
}

export const hm = (min: number) => `${min < 0 ? "−" : ""}${Math.floor(Math.abs(min) / 60)}:${String(Math.abs(min) % 60).padStart(2, "0")} h`;
const hms = (sec: number) => {
  const s = Math.max(0, Math.floor(sec));
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const time = (iso: string) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
const dayLabel = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });

/** Gesetzliche Pause (§ 4 ArbZG bzw. § 11 JArbSchG) – gleiche Formel wie im Backend. */
function legalBreak(presence: number, minor: boolean): number {
  if (minor) return Math.min(30, Math.max(0, presence - 270)) + Math.min(30, Math.max(0, presence - 390));
  return Math.min(30, Math.max(0, presence - 360)) + Math.min(15, Math.max(0, presence - 570));
}

/** Stempeluhr mit großem Check-In/Check-Out-Kreis. ``base`` = /api/me oder /api/portal/employee. */
export function TimeClock({ base, fetcher, compact = false }: { base: string; fetcher: Fetcher; compact?: boolean }) {
  const [st, setSt] = useState<ClockStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const { toast } = useFeedback();

  const load = useCallback(() => {
    fetcher<ClockStatus>("GET", `${base}/clock`)
      .then((s) => { setSt(s); setOffset(new Date(s.server_time).getTime() - Date.now()); setError(null); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Stempeluhr nicht erreichbar"));
  }, [base, fetcher]);
  useEffect(load, [load]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    const r = setInterval(load, 5 * 60_000);
    return () => { clearInterval(t); clearInterval(r); };
  }, [load]);

  if (error) return <Alert kind="info">{error}</Alert>;
  if (!st) return <Card className="grid place-items-center p-10"><Skeleton className="size-56 rounded-full" /></Card>;

  const running = !!st.open;
  const serverNow = now + offset;
  const sessionSec = running ? (serverNow - new Date(st.open!.started_at).getTime()) / 1000 : 0;
  // heutige Anwesenheit live: abgeschlossene Blöcke (vom Server) + laufender Block
  const loadedAt = new Date(st.server_time).getTime();
  const presenceLive = st.today.presence + (running ? Math.max(0, (serverNow - loadedAt) / 60000) : 0);
  const pLive = Math.floor(presenceLive);
  const breakLive = legalBreak(pLive, st.minor);
  const netLive = pLive - breakLive;
  const workDaysPerWeek = st.employee.work_days.length || 5;
  const dailyTarget = st.week.target ? Math.round(st.week.target / workDaysPerWeek) : 480;
  const progress = Math.min(1, netLive / Math.max(1, dailyTarget));
  const weekNet = st.week.net + (running ? netLive - st.today.net : 0);

  const toggle = () => {
    setBusy(true);
    fetcher("POST", `${base}/clock/${running ? "out" : "in"}`, running ? {} : undefined)
      .then(() => { toast(running ? "Ausgestempelt – schönen Feierabend!" : "Eingestempelt – die Zeit läuft."); load(); })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Stempeln fehlgeschlagen", "error"))
      .finally(() => setBusy(false));
  };

  const size = compact ? 208 : 248;
  const R = size / 2 - 10;
  const C = 2 * Math.PI * R;

  return (
    <div className={compact ? "space-y-4" : "grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"}>
      <Card className="flex flex-col items-center gap-5 px-4 py-8">
        <div className="relative" style={{ width: size, height: size }}>
          <svg viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 -rotate-90" aria-hidden>
            <circle cx={size / 2} cy={size / 2} r={R} fill="none" strokeWidth={8} className="stroke-slate-200 dark:stroke-slate-800" />
            <circle cx={size / 2} cy={size / 2} r={R} fill="none" strokeWidth={8} strokeLinecap="round"
              strokeDasharray={C} strokeDashoffset={C * (1 - progress)}
              className={`transition-[stroke-dashoffset] duration-700 ${running ? "stroke-rose-400" : "stroke-emerald-400"}`} />
          </svg>
          {running && <span className="absolute inset-5 animate-ping rounded-full bg-rose-500/20 [animation-duration:2.4s]" aria-hidden />}
          {!running && <span className="absolute inset-5 animate-pulse rounded-full bg-emerald-400/25 [animation-duration:2.8s]" aria-hidden />}
          <button type="button" onClick={toggle} disabled={busy}
            aria-label={running ? "Check Out – ausstempeln" : "Check In – einstempeln"}
            className={`absolute inset-5 flex flex-col items-center justify-center rounded-full text-white shadow-xl ring-4 ring-white/70 transition
              duration-300 hover:scale-[1.03] active:scale-95 disabled:opacity-70 dark:ring-white/10
              ${running
                ? "bg-[radial-gradient(circle_at_30%_25%,#fb7185,#e11d48_55%,#9f1239)] shadow-rose-600/40"
                : "bg-[radial-gradient(circle_at_30%_25%,#6ee7b7,#10b981_55%,#047857)] shadow-emerald-600/40"}`}>
            <svg viewBox="0 0 24 24" className="mb-1 size-8 opacity-90" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden>
              {running ? <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /> : <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3" />}
            </svg>
            <span className={`${compact ? "text-2xl" : "text-3xl"} font-semibold tracking-tight`}>{running ? "Check Out" : "Check In"}</span>
            <span className="mt-1 font-mono text-sm tabular-nums opacity-90">{running ? hms(sessionSec) : "bereit"}</span>
          </button>
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
            {running ? `Eingestempelt seit ${time(st.open!.started_at)} Uhr` : "Nicht eingestempelt"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Heute {hm(netLive)} Arbeitszeit · Ziel {hm(dailyTarget)}
            {breakLive > 0 && <> · {breakLive} Min. Pause abgezogen</>}
          </p>
        </div>
        {st.warning && <Alert kind="warning">{st.warning}</Alert>}
      </Card>

      <div className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <Mini label="Heute" value={hm(netLive)} hint={`${hm(pLive)} anwesend`} />
          <Mini label="Woche" value={hm(weekNet)} hint={st.week.target ? `von ${hm(st.week.target)}` : "kein Soll"} />
          {st.week.target && weekNet > st.week.target
            ? <Mini label="Mehrarbeit" value={`+${hm(weekNet - st.week.target)}`} hint="über dem Wochensoll" tone="green" />
            : <Mini label="Noch offen" value={st.week.target ? hm(Math.max(0, st.week.target - weekNet)) : "–"} hint="bis zum Wochensoll" />}
        </div>
        <Card>
          <CardHeader title="Pausenregel" description={st.minor
            ? "Jugendarbeitsschutz: ab 4,5 Std. 30 Min., ab 6 Std. 60 Min."
            : "Arbeitszeitgesetz: ab 6 Std. 30 Min., ab 9 Std. 45 Min. – gilt für Minijob, Teilzeit und Vollzeit gleich."} />
          <p className="px-5 pb-4 text-xs text-slate-500">Die Pause wird automatisch auf die Tagessumme berechnet. Mehrere Ein-/Ausstempelungen am Tag zählen zusammen.</p>
        </Card>
        {!compact && (
          <Card>
            <CardHeader title="Letzte Tage" />
            {st.days.length === 0 ? <div className="p-5"><Empty>Noch keine Stempelzeiten.</Empty></div> : (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {st.days.map((d) => (
                  <li key={d.date} className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm">
                    <span className="w-24 shrink-0 font-medium text-slate-700 dark:text-slate-300">{dayLabel(d.date)}</span>
                    <span className="min-w-0 flex-1 truncate text-xs text-slate-500">
                      {d.entries.slice().reverse().map((e) => `${time(e.started_at)}–${e.ended_at ? time(e.ended_at) : "läuft"}`).join(", ")}
                    </span>
                    <span className="shrink-0 font-mono tabular-nums">{hm(d.net)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

function Mini({ label, value, hint, tone = "slate" }: { label: string; value: string; hint?: string; tone?: "slate" | "green" }) {
  return (
    <Card className="px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 font-mono text-lg font-semibold tabular-nums ${tone === "green" ? "text-emerald-600 dark:text-emerald-400" : "text-slate-900 dark:text-slate-100"}`}>{value}</p>
      {hint && <p className="truncate text-[11px] text-slate-500">{hint}</p>}
    </Card>
  );
}

/** Kompakte Stempeluhr fürs Dashboard: erscheint nur, wenn das Konto ein Mitarbeiterprofil hat. */
export function ClockChip({ fetcher }: { fetcher: Fetcher }) {
  const [st, setSt] = useState<ClockStatus | null>(null);
  const [offset, setOffset] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const { toast } = useFeedback();
  const load = useCallback(() => {
    fetcher<ClockStatus>("GET", "/api/me/clock").then((s) => { setSt(s); setOffset(new Date(s.server_time).getTime() - Date.now()); }).catch(() => setSt(null));
  }, [fetcher]);
  useEffect(load, [load]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  if (!st) return null;
  const running = !!st.open;
  const sec = running ? (now + offset - new Date(st.open!.started_at).getTime()) / 1000 : 0;
  const toggle = () => {
    setBusy(true);
    fetcher("POST", `/api/me/clock/${running ? "out" : "in"}`, running ? {} : undefined)
      .then(() => { toast(running ? "Ausgestempelt – schönen Feierabend!" : "Eingestempelt – die Zeit läuft."); load(); })
      .catch((e: unknown) => toast(e instanceof HttpError ? e.message : "Stempeln fehlgeschlagen", "error"))
      .finally(() => setBusy(false));
  };
  return (
    <Card className="flex flex-wrap items-center gap-4 px-5 py-4">
      <button type="button" onClick={toggle} disabled={busy} aria-label={running ? "Check Out" : "Check In"}
        className={`relative grid size-20 shrink-0 place-items-center rounded-full text-sm font-semibold text-white shadow-lg ring-4 ring-white/70 transition hover:scale-105 active:scale-95 dark:ring-white/10
          ${running ? "bg-[radial-gradient(circle_at_30%_25%,#fb7185,#e11d48_55%,#9f1239)] shadow-rose-600/30" : "bg-[radial-gradient(circle_at_30%_25%,#6ee7b7,#10b981_55%,#047857)] shadow-emerald-600/30"}`}>
        <span className="text-center leading-tight">{running ? "Check Out" : "Check In"}</span>
      </button>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-slate-900 dark:text-slate-100">{running ? `Eingestempelt seit ${time(st.open!.started_at)} Uhr` : "Guten Tag! Bereit zum Einstempeln?"}</p>
        <p className="text-sm text-slate-500">{running ? <span className="font-mono tabular-nums">{hms(sec)}</span> : <>Heute {hm(st.today.net)} · Woche {hm(st.week.net)}</>}</p>
      </div>
      <a href="/stempeluhr" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-300">Zur Stempeluhr</a>
    </Card>
  );
}
