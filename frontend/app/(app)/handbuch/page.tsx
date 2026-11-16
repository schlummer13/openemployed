"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { Markdown } from "@/components/Markdown";
import { Alert, Button, Card, PageSkeleton } from "@/components/ui";
import { api } from "@/lib/http";

interface Toc { slug: string; title: string; summary: string; sections: { anchor: string; title: string }[] }
interface Hit { chapter: string; chapter_title: string; anchor: string; title: string; snippet: string; score: number }
interface Answer { answer: string | null; sources: string[]; hits: Hit[]; error?: string }

function Snippet({ text }: { text: string }) {
  return <>{text.split(/(«[^»]*»)/g).map((p, i) => p.startsWith("«") ? <mark key={i} className="rounded bg-amber-100 px-0.5 text-slate-900 dark:bg-amber-500/30 dark:text-white">{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>)}</>;
}

export default function HandbookPage() {
  const [toc, setToc] = useState<Toc[] | null>(null);
  const [slug, setSlug] = useState<string>("erste-schritte");
  const [doc, setDoc] = useState<{ title: string; summary: string; markdown: string } | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [asking, setAsking] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const titleOf = (s: string) => toc?.find((c) => c.slug === s.split("#")[0])?.sections.find((x) => x.anchor === s.split("#")[1])?.title ?? s;

  useEffect(() => {
    void api<{ chapters: Toc[] }>("GET", "/api/help").then((r) => setToc(r.chapters));
    const p = new URLSearchParams(window.location.search);
    if (p.get("k")) setSlug(p.get("k")!);
    if (p.get("q")) { setQ(p.get("q")!); }
  }, []);
  const open = useCallback((s: string, anchor?: string) => {
    setSlug(s); setHits(null); setAnswer(null);
    const url = `/handbuch?k=${s}${anchor ? `#${anchor}` : ""}`;
    window.history.replaceState(null, "", url);
    if (anchor) setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
    else window.scrollTo({ top: 0 });
  }, []);
  useEffect(() => {
    void api<{ title: string; summary: string; markdown: string }>("GET", `/api/help/chapters/${slug}`).then((d) => {
      setDoc(d);
      const anchor = window.location.hash.slice(1);
      if (anchor) setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ block: "start" }), 120);
    }).catch(() => setDoc(null));
  }, [slug]);
  const onSearch = (v: string) => {
    setQ(v); setAnswer(null);
    if (timer.current) clearTimeout(timer.current);
    if (!v.trim()) { setHits(null); return; }
    timer.current = setTimeout(() => { void api<{ results: Hit[] }>("GET", `/api/help/search?q=${encodeURIComponent(v)}`).then((r) => setHits(r.results)); }, 180);
  };
  useEffect(() => { if (q && hits === null) onSearch(q); }, [toc]); // eslint-disable-line react-hooks/exhaustive-deps
  const ask = async () => {
    if (q.trim().length < 3) return;
    setAsking(true);
    try { setAnswer(await api<Answer>("POST", "/api/help/ask", { question: q })); } finally { setAsking(false); }
  };

  if (!toc) return <PageSkeleton stats={0} />;
  const current = toc.find((c) => c.slug === slug);
  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto">
        <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Handbuch</p>
        <nav className="grid gap-0.5 text-sm">
          {toc.map((c) => (
            <div key={c.slug}>
              <button type="button" onClick={() => open(c.slug)} className={`w-full rounded-lg px-2.5 py-1.5 text-left ${c.slug === slug ? "bg-white font-medium shadow-card dark:bg-white/[0.06]" : "text-slate-600 hover:bg-slate-200/50 dark:text-slate-400 dark:hover:bg-white/[0.04]"}`}>{c.title}</button>
              {c.slug === slug && <div className="mb-1 ml-3 grid border-l border-slate-200 pl-2 dark:border-white/10">{c.sections.map((s) => <button key={s.anchor} type="button" onClick={() => open(c.slug, s.anchor)} className="truncate py-1 text-left text-xs text-slate-500 hover:text-slate-900 dark:hover:text-white">{s.title}</button>)}</div>}
            </div>
          ))}
        </nav>
      </aside>
      <div className="min-w-0">
        <Card className="mb-6">
          <div className="flex flex-wrap gap-2">
            <div className="relative min-w-0 flex-1">
              <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <input type="search" aria-label="Handbuch durchsuchen" value={q} onChange={(e) => onSearch(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void ask(); }}
                placeholder="Suchen oder fragen, z. B. „Wie rechne ich Gehälter ab?“"
                className="h-11 w-full rounded-xl border-0 bg-slate-50 pl-10 pr-3 text-[15px] shadow-[0_0_0_1px_rgb(15_15_25/0.08)] outline-none focus:bg-white focus:shadow-[0_0_0_1px_var(--color-brand-500),0_0_0_4px_rgb(18_160_147/0.15)] dark:bg-white/5 dark:focus:bg-slate-900" />
            </div>
            <Button variant="primary" className="!h-11" disabled={asking || q.trim().length < 3} onClick={() => void ask()}>{asking ? "Denkt nach …" : "Frag das Handbuch"}</Button>
          </div>
          {answer && (
            <div className="mt-4 rounded-xl bg-brand-50/60 p-4 dark:bg-brand-500/10">
              {answer.answer ? <>
                <p className="whitespace-pre-line text-[14.5px] leading-relaxed">{answer.answer}</p>
                {answer.sources.length > 0 && <p className="mt-3 flex flex-wrap gap-1.5 text-xs">Quellen: {answer.sources.map((s) => <button key={s} type="button" onClick={() => open(s.split("#")[0]!, s.split("#")[1])} className="rounded-full bg-white px-2 py-0.5 font-medium text-brand-700 shadow-card hover:underline dark:bg-slate-900 dark:text-brand-300">{titleOf(s)}</button>)}</p>}
                <p className="mt-2 text-[11px] text-slate-500">Antwort der lokalen KI auf Basis des Handbuchs – im Zweifel den verlinkten Abschnitt lesen.</p>
              </> : <p className="text-sm text-slate-600 dark:text-slate-300">{answer.error ?? "Die KI ist nicht eingerichtet"} – hier sind die passendsten Abschnitte:</p>}
            </div>
          )}
          {hits && (
            hits.length === 0 ? <p className="mt-4 text-sm text-slate-500">Nichts gefunden – andere Begriffe probieren oder „Frag das Handbuch“.</p> : (
              <ul className="mt-4 divide-y divide-slate-100 dark:divide-white/5">
                {hits.slice(0, 8).map((h) => (
                  <li key={h.chapter + h.anchor}>
                    <button type="button" onClick={() => open(h.chapter, h.anchor || undefined)} className="block w-full py-2.5 text-left hover:bg-slate-50 dark:hover:bg-white/[0.03]">
                      <span className="block text-sm font-medium">{h.title} <span className="font-normal text-slate-400">· {h.chapter_title}</span></span>
                      <span className="block text-xs leading-relaxed text-slate-500"><Snippet text={h.snippet} /></span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          )}
        </Card>
        <Card className="!px-6 !py-7 sm:!px-10">
          {doc ? <><p className="text-[11px] font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-300">Handbuch</p><Markdown source={doc.markdown} /></> : <Alert>Kapitel nicht gefunden.</Alert>}
          {current && (() => { const idx = toc.indexOf(current); const next = toc[idx + 1]; const prev = toc[idx - 1]; return (
            <div className="mt-10 flex flex-wrap justify-between gap-3 border-t border-slate-100 pt-5 text-sm dark:border-white/5">
              {prev ? <button type="button" onClick={() => open(prev.slug)} className="text-left hover:underline">← {prev.title}</button> : <span />}
              {next && <button type="button" onClick={() => open(next.slug)} className="text-right font-medium text-brand-700 hover:underline dark:text-brand-300">{next.title} →</button>}
            </div>
          ); })()}
        </Card>
        <p className="mt-4 text-xs text-slate-500">Für Entwickler: <Link className="text-brand-700 hover:underline dark:text-brand-300" href="/entwickler">REST-API-Referenz</Link> (nur Geschäftsführung).</p>
      </div>
    </div>
  );
}
