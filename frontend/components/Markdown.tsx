import { Fragment, type ReactNode } from "react";

/** Schlanker Markdown-Renderer für das Handbuch (Überschriften, Absätze, Listen, Code, Zitate, fett, Inline-Code, Links). */
export function slugify(text: string): string {
  return text.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss").normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let last = 0; let m: RegExpExecArray | null; let i = key;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("`")) out.push(<code key={i++} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/10">{t.slice(1, -1)}</code>);
    else if (t.startsWith("**")) out.push(<strong key={i++} className="font-semibold">{t.slice(2, -2)}</strong>);
    else {
      const mm = t.match(/^\[([^\]]+)\]\(([^)]+)\)$/)!;
      const href = mm[2]!;
      const safe = /^(https?:|\/|#|mailto:)/.test(href) ? href : "#";
      out.push(<a key={i++} href={safe} className="font-medium text-brand-700 underline decoration-brand-300 underline-offset-2 hover:decoration-brand-600 dark:text-brand-300" {...(safe.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}>{mm[1]}</a>);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source, skipTitle = false }: { source: string; skipTitle?: boolean }) {
  const lines = source.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0; let k = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim(); const code: string[] = []; i++;
      while (i < lines.length && !lines[i]!.startsWith("```")) code.push(lines[i++]!);
      i++;
      blocks.push(<pre key={k++} className="my-4 overflow-x-auto rounded-xl bg-slate-900 px-4 py-3 text-[12.5px] leading-relaxed text-slate-100"><code data-lang={lang}>{code.join("\n")}</code></pre>);
      continue;
    }
    if (line.startsWith("# ")) { if (!skipTitle) blocks.push(<h1 key={k++} className="mb-2 text-2xl font-semibold tracking-tight">{line.slice(2)}</h1>); i++; continue; }
    if (line.startsWith("## ")) { const t = line.slice(3); blocks.push(<h2 key={k++} id={slugify(t)} className="mt-10 scroll-mt-24 border-t border-slate-100 pt-6 text-lg font-semibold first:mt-4 first:border-0 first:pt-0 dark:border-white/5">{t}</h2>); i++; continue; }
    if (line.startsWith("### ")) { const t = line.slice(4); blocks.push(<h3 key={k++} id={slugify(t)} className="mt-6 scroll-mt-24 font-semibold">{t}</h3>); i++; continue; }
    if (line.startsWith("> ")) { blocks.push(<p key={k++} className="mb-6 text-[15px] text-slate-500">{inline(line.slice(2))}</p>); i++; continue; }
    if (/^\s*(- |\d+\. )/.test(line)) {
      const ordered = /^\s*\d+\. /.test(line); const items: string[] = [];
      while (i < lines.length && /^\s*(- |\d+\. )|^\s{2,}\S/.test(lines[i]!)) {
        const l = lines[i]!;
        if (/^\s*(- |\d+\. )/.test(l)) items.push(l.replace(/^\s*(- |\d+\. )/, "")); else items[items.length - 1] += " " + l.trim();
        i++;
      }
      const Tag = ordered ? "ol" : "ul";
      blocks.push(<Tag key={k++} className={`my-3 space-y-1.5 pl-5 text-[14.5px] leading-relaxed ${ordered ? "list-decimal" : "list-disc"} marker:text-slate-400`}>{items.map((it, j) => <li key={j}>{inline(it)}</li>)}</Tag>);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^(#|>|```|\s*- |\s*\d+\. )/.test(lines[i]!)) para.push(lines[i++]!);
    blocks.push(<p key={k++} className="my-3 text-[14.5px] leading-relaxed text-slate-700 dark:text-slate-300">{inline(para.join(" "))}</p>);
  }
  return <div>{blocks.map((b, j) => <Fragment key={j}>{b}</Fragment>)}</div>;
}
