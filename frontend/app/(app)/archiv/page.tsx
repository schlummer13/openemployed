"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { ArchiveMirror } from "@/components/ArchiveMirror";
import { Alert, Badge, Button, Card, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Tabs, Textarea } from "@/components/ui";
import { api, formatDate, HttpError, todayIso, upload } from "@/lib/http";

interface Doc {
  id: string; seq: number; number: string; category: string; title: string; document_date: string; retention_years: number; retain_until: string;
  filename: string; mime_type: string; size: number; sha256: string; source_type: string | null; party: string | null; tags: string[]; notes: string | null;
  purged_at: string | null; created_by: string; created_at: string; snippet: string | null; chain_hash?: string;
}
interface Result { total: number; documents: Doc[]; by_category: Record<string, number>; categories: Record<string, { label: string; years: number }>; retention_due: number }

const SOURCE: Record<string, string> = { invoice: "Rechnung", receipt: "Beleg", dunning: "Mahnwesen", signature: "Signatur", sepa: "Zahlungsverkehr", payslip: "Lohn", offer: "Angebot", mail: "E-Mail-Versand" };
const size = (b: number) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

/** Treffer-Ausschnitt mit «Markierung» sicher als Text rendern (kein HTML aus dem Dokument). */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/(«[^»]*»)/g);
  return <span className="block text-xs text-slate-500">{parts.map((p, i) => p.startsWith("«") ? <mark key={i} className="rounded bg-amber-100 px-0.5 text-slate-900 dark:bg-amber-500/30 dark:text-white">{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>)}</span>;
}

export default function ArchivePage() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [year, setYear] = useState("");
  const [data, setData] = useState<Result | null>(null);
  const [view, setView] = useState<"docs" | "mirror">("docs");
  const [detail, setDetail] = useState<Doc | null>(null);
  const [uploadForm, setUploadForm] = useState<{ file: File | null; category: string; title: string; document_date: string; party: string; notes: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useFeedback();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback((query: string) => {
    const p = new URLSearchParams();
    if (query.trim()) p.set("q", query.trim());
    if (category) p.set("category", category);
    if (year) p.set("year", year);
    void api<Result>("GET", `/api/archive?${p}`).then(setData);
  }, [category, year]);
  useEffect(() => { load(q); }, [load]); // eslint-disable-line react-hooks/exhaustive-deps
  const onSearch = (v: string) => { setQ(v); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => load(v), 250); };

  const sync = async () => {
    setBusy(true);
    try { const r = await api<Record<string, number>>("POST", "/api/archive/sync"); const n = Object.values(r).reduce((a, b) => a + b, 0); toast(n ? `${n} Dokumente übernommen.` : "Archiv ist aktuell."); load(q); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Übernahme fehlgeschlagen", "error"); } finally { setBusy(false); }
  };
  const verify = async () => {
    const r = await api<{ ok: boolean; checked: number; problems: string[] }>("GET", "/api/archive/verify");
    toast(r.ok ? `Integrität geprüft: ${r.checked} Dokumente, Hash-Kette lückenlos.` : `Abweichung: ${r.problems.join(", ")}`, r.ok ? "success" : "error");
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!uploadForm?.file) return;
    setError(null); setBusy(true);
    try {
      await upload("/api/archive", uploadForm.file, { category: uploadForm.category, title: uploadForm.title, document_date: uploadForm.document_date,
        ...(uploadForm.party ? { party: uploadForm.party } : {}), ...(uploadForm.notes ? { notes: uploadForm.notes } : {}) });
      toast("Dokument archiviert – ab jetzt unveränderlich."); setUploadForm(null); load(q);
    } catch (err) { setError(err instanceof HttpError ? err.message : "Archivieren fehlgeschlagen"); } finally { setBusy(false); }
  };
  const purge = async (d: Doc) => {
    if (!(await confirm({ title: `Inhalt von ${d.number} vernichten?`, tone: "danger", confirmLabel: "Endgültig vernichten",
      message: `Die Aufbewahrungsfrist ist am ${formatDate(d.retain_until)} abgelaufen. Der Inhalt wird unwiderruflich gelöscht; Metadaten und Prüfsumme bleiben als Nachweis.` }))) return;
    try { await api("POST", `/api/archive/${d.id}/purge`); toast("Inhalt vernichtet."); setDetail(null); load(q); }
    catch (e) { toast(e instanceof HttpError ? e.message : "Vernichten fehlgeschlagen", "error"); }
  };

  if (!data) return <PageSkeleton stats={0} />;
  const cats = Object.entries(data.categories);
  const years = Array.from({ length: 11 }, (_, i) => new Date().getFullYear() - i);
  return (
    <>
      <PageHeader title="Dokumentenarchiv" eyebrow="System"
        description="Revisionssicher nach GoBD: jedes Dokument mit Prüfsumme in einer Hash-Kette, unveränderlich und mit gesetzlicher Aufbewahrungsfrist. Rechnungen, Belege, Verträge, Lohnabrechnungen, SEPA-Dateien und versendete E-Mails kommen automatisch hinzu."
        actions={<>
          <Button onClick={() => void verify()}>Integrität prüfen</Button>
          <Button onClick={() => void sync()} disabled={busy}>Jetzt übernehmen</Button>
          <Button variant="primary" onClick={() => setUploadForm({ file: null, category: "correspondence", title: "", document_date: todayIso(), party: "", notes: "" })}>+ Dokument</Button>
        </>} />
      <Tabs value={view} onChange={setView} items={[{ value: "docs", label: "Dokumente" }, { value: "mirror", label: "Spiegelung & Export" }]} />
      {view === "mirror" ? <ArchiveMirror /> : <>
      {data.retention_due > 0 && <div className="mb-4"><Alert kind="info">{data.retention_due} Dokumente haben ihre Aufbewahrungsfrist überschritten und können vernichtet werden (personenbezogene Daten: DSGVO-Löschpflicht prüfen).</Alert></div>}
      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <Input type="search" aria-label="Archiv durchsuchen" placeholder="Volltextsuche, z. B. Mietvertrag, RE-2026-0012, Telekom …" value={q} onChange={(e) => onSearch(e.target.value)} className="min-w-0 flex-1" />
          <Select aria-label="Jahr" value={year} onChange={(e) => setYear(e.target.value)} className="!w-28"><option value="">alle Jahre</option>{years.map((y) => <option key={y}>{y}</option>)}</Select>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <button type="button" onClick={() => setCategory("")} className={`rounded-full px-3 py-1 text-xs font-medium ${category === "" ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-white/5 dark:text-slate-300"}`}>Alle</button>
          {cats.filter(([k]) => data.by_category[k]).map(([k, c]) => (
            <button key={k} type="button" onClick={() => setCategory(k === category ? "" : k)} className={`rounded-full px-3 py-1 text-xs font-medium ${category === k ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-white/5 dark:text-slate-300"}`}>{c.label} · {data.by_category[k]}</button>
          ))}
        </div>
      </Card>
      <Card className="!p-0">
        {data.documents.length === 0 ? <div className="p-6"><Empty>{q ? "Keine Treffer." : "Noch keine Dokumente – „Jetzt übernehmen“ holt vorhandene Rechnungen und Belege."}</Empty></div> : (
          <ul className="divide-y divide-slate-100 dark:divide-white/5">
            {data.documents.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => setDetail(d)} className="flex w-full items-start gap-4 px-5 py-3.5 text-left hover:bg-slate-50 sm:px-6 dark:hover:bg-white/[0.03]">
                  <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-[10px] font-semibold uppercase text-slate-500 dark:bg-white/5">{d.mime_type === "message/rfc822" ? "eml" : (d.filename.split(".").pop() ?? "").slice(0, 4)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{d.title}</span>
                    <span className="block text-xs text-slate-500">{d.number} · {data.categories[d.category]?.label}{d.party ? ` · ${d.party}` : ""}{d.source_type ? ` · aus ${SOURCE[d.source_type] ?? d.source_type}` : ""}</span>
                    {d.snippet && <Snippet text={d.snippet} />}
                  </span>
                  <span className="shrink-0 text-right text-xs text-slate-500"><span className="block tabular text-slate-700 dark:text-slate-300">{formatDate(d.document_date)}</span>{d.purged_at ? "vernichtet" : `bis ${new Date(d.retain_until).getFullYear()}`}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {data.total > data.documents.length && <p className="border-t border-slate-100 px-6 py-3 text-xs text-slate-500 dark:border-white/5">{data.documents.length} von {data.total} – Suche verfeinern</p>}
      </Card>

      <Drawer open={!!detail} onClose={() => setDetail(null)} title={detail?.title ?? ""} subtitle={detail ? `${detail.number} · ${data.categories[detail.category]?.label}` : ""}
        footer={detail && <>
          {detail.purged_at === null && new Date(detail.retain_until) < new Date() && <Button variant="ghost" className="mr-auto" onClick={() => void purge(detail)}>Vernichten</Button>}
          {detail.purged_at === null && <a className="inline-flex h-9 items-center rounded-lg bg-brand-600 px-3.5 text-sm font-medium text-white hover:bg-brand-700" href={`/api/archive/${detail.id}/file`} target="_blank" rel="noreferrer">Öffnen</a>}
        </>}>
        {detail && (
          <dl className="grid gap-3 text-sm">
            {[["Dokumentdatum", formatDate(detail.document_date)], ["Partner", detail.party ?? "–"], ["Datei", `${detail.filename} (${size(detail.size)})`],
              ["Aufbewahrung", `${detail.retention_years} Jahre – bis ${formatDate(detail.retain_until)}`], ["Archiviert", `${formatDate(detail.created_at)} von ${detail.created_by}`],
              ["Herkunft", detail.source_type ? SOURCE[detail.source_type] ?? detail.source_type : "manueller Upload"]].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3"><dt className="text-slate-500">{k}</dt><dd className="break-words">{v}</dd></div>
            ))}
            <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3"><dt className="text-slate-500">SHA-256</dt><dd className="break-all font-mono text-[11px]">{detail.sha256}</dd></div>
            {detail.tags.length > 0 && <div className="flex flex-wrap gap-1">{detail.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div>}
            {detail.notes && <p className="whitespace-pre-line text-slate-600 dark:text-slate-300">{detail.notes}</p>}
            {detail.purged_at && <Alert kind="info">Inhalt am {formatDate(detail.purged_at)} nach Fristablauf vernichtet.</Alert>}
          </dl>
        )}
      </Drawer>

      <Drawer open={!!uploadForm} onClose={() => { setUploadForm(null); setError(null); }} title="Dokument archivieren" subtitle="Nach dem Speichern unveränderlich"
        footer={<><Button onClick={() => setUploadForm(null)}>Abbrechen</Button><Button type="submit" form="archive-form" variant="primary" disabled={busy || !uploadForm?.file}>Archivieren</Button></>}>
        {uploadForm && (
          <form id="archive-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <Field label="Datei" className="sm:col-span-2" hint="PDF, Bild, E-Mail (.eml), XML, Word, Excel – max. 25 MB">
              <input type="file" required className="block w-full text-sm" onChange={(e) => { const f = e.target.files?.[0] ?? null; setUploadForm({ ...uploadForm, file: f, title: uploadForm.title || (f?.name.replace(/\.[^.]+$/, "") ?? "") }); }} />
            </Field>
            <Field label="Titel" className="sm:col-span-2"><Input required value={uploadForm.title} onChange={(e) => setUploadForm({ ...uploadForm, title: e.target.value })} /></Field>
            <Field label="Kategorie" hint={`Frist ${data.categories[uploadForm.category]?.years} Jahre`}><Select value={uploadForm.category} onChange={(e) => setUploadForm({ ...uploadForm, category: e.target.value })}>{cats.map(([k, c]) => <option key={k} value={k}>{c.label}</option>)}</Select></Field>
            <Field label="Dokumentdatum"><Input type="date" required max={todayIso()} value={uploadForm.document_date} onChange={(e) => setUploadForm({ ...uploadForm, document_date: e.target.value })} /></Field>
            <Field label="Partner (optional)" className="sm:col-span-2"><Input value={uploadForm.party} onChange={(e) => setUploadForm({ ...uploadForm, party: e.target.value })} /></Field>
            <Field label="Notiz (optional)" className="sm:col-span-2"><Textarea rows={2} value={uploadForm.notes} onChange={(e) => setUploadForm({ ...uploadForm, notes: e.target.value })} /></Field>
            {error && <div className="sm:col-span-2"><Alert>{error}</Alert></div>}
          </form>
        )}
      </Drawer>
      </>}
    </>
  );
}
