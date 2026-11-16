"use client";

import { useEffect, useMemo, useState } from "react";
import { useUser } from "@/components/AuthGate";
import { Alert, Badge, Card, CardHeader, PageHeader, PageSkeleton } from "@/components/ui";
import { api, HttpError } from "@/lib/http";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Schema = Record<string, any>;
interface Op { method: string; path: string; summary?: string; description?: string; tags?: string[]; parameters?: any[]; requestBody?: any; responses?: Record<string, any> }

const METHOD: Record<string, string> = { get: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300", post: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  put: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300", delete: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300", patch: "bg-violet-100 text-violet-800" };

function resolve(spec: Schema, s: Schema | undefined, depth = 0): Schema {
  if (!s || depth > 6) return {};
  if (s.$ref) return resolve(spec, s.$ref.split("/").slice(1).reduce((o: any, k: string) => o?.[k], spec), depth + 1);
  if (s.anyOf) { const nn = s.anyOf.filter((x: Schema) => x.type !== "null"); return { ...resolve(spec, nn[0] ?? {}, depth + 1), nullable: s.anyOf.length > nn.length }; }
  if (s.allOf) return resolve(spec, s.allOf[0], depth + 1);
  return s;
}

function example(spec: Schema, s: Schema | undefined, depth = 0): unknown {
  const r = resolve(spec, s);
  if (depth > 4) return null;
  if (r.example !== undefined) return r.example;
  if (r.default !== undefined) return r.default;
  if (r.enum) return r.enum[0];
  switch (r.type) {
    case "object": return Object.fromEntries(Object.entries(r.properties ?? {}).map(([k, v]) => [k, example(spec, v as Schema, depth + 1)]));
    case "array": return [example(spec, r.items, depth + 1)];
    case "integer": return r.minimum ?? 0;
    case "number": return 0;
    case "boolean": return false;
    case "string":
      if (r.format === "date") return "2026-01-31";
      if (r.format === "date-time") return "2026-01-31T12:00:00Z";
      if (r.format === "uuid") return "00000000-0000-0000-0000-000000000000";
      if (r.pattern?.includes("[0-9]{4}-")) return "2026-01";
      return r.title ? String(r.title).toLowerCase() : "text";
    default: return r.properties ? example(spec, { ...r, type: "object" }, depth) : null;
  }
}

function Fields({ spec, schema }: { spec: Schema; schema: Schema }) {
  const r = resolve(spec, schema);
  const props = Object.entries(r.properties ?? {});
  if (!props.length) return null;
  return (
    <table className="mt-2 w-full text-xs">
      <tbody>
        {props.map(([k, v]) => { const f = resolve(spec, v as Schema); return (
          <tr key={k} className="border-t border-slate-100 align-top dark:border-white/5">
            <td className="py-1.5 pr-3 font-mono">{k}{r.required?.includes(k) && <span className="text-red-500">*</span>}</td>
            <td className="py-1.5 pr-3 text-slate-500">{f.type ?? "object"}{f.format ? ` (${f.format})` : ""}{f.enum ? `: ${f.enum.join(" | ")}` : ""}{f.nullable ? " · optional" : ""}</td>
            <td className="py-1.5 text-slate-500">{[f.description, f.maxLength ? `max. ${f.maxLength} Zeichen` : null, f.pattern ? `Muster ${f.pattern}` : null].filter(Boolean).join(" · ")}</td>
          </tr>
        ); })}
      </tbody>
    </table>
  );
}

export default function DeveloperPage() {
  const me = useUser();
  const [spec, setSpec] = useState<Schema | null>(null);
  const [events, setEvents] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState<string>("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    void api<Schema>("GET", "/api/admin/openapi.json").then(setSpec).catch((e) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
    void api<{ events: Record<string, string> }>("GET", "/api/admin/webhooks").then((r) => setEvents(r.events)).catch(() => undefined);
  }, []);
  const ops = useMemo<Op[]>(() => !spec ? [] : Object.entries(spec.paths ?? {}).flatMap(([path, methods]: [string, any]) =>
    Object.entries(methods).map(([method, op]: [string, any]) => ({ method, path, ...op }))).filter((o) => !o.path.startsWith("/api/auth/dev")), [spec]);
  const tags = useMemo(() => [...new Set(ops.flatMap((o) => o.tags ?? ["sonstiges"]))].sort(), [ops]);
  const filtered = ops.filter((o) => (!tag || (o.tags ?? ["sonstiges"]).includes(tag)) &&
    (!q || `${o.method} ${o.path} ${o.summary ?? ""} ${o.description ?? ""}`.toLowerCase().includes(q.toLowerCase())));

  if (me && me.permissions?.admin !== "write") return <Alert>Die Entwickler-API-Referenz ist nur für die Geschäftsführung freigegeben.</Alert>;
  if (error) return <Alert>{error}</Alert>;
  if (!spec) return <PageSkeleton stats={0} />;
  const base = typeof window !== "undefined" ? window.location.origin : "";
  return (
    <>
      <PageHeader title="Entwickler-API" eyebrow="System" description={`REST-API von ${spec.info?.title ?? "OpenEmployed"} – ${ops.length} Endpunkte, erzeugt aus der laufenden Version. Nur für die Geschäftsführung sichtbar.`} />
      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Authentifizierung" />
          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">API-Schlüssel unter <em>System &amp; Integrationen → API-Schlüssel</em> anlegen und als Bearer-Token senden. Schlüssel mit „nur lesen“ dürfen ausschließlich GET; Verwaltungsendpunkte (<code>/api/admin/…</code>) sind für Schlüssel gesperrt. Antworten sind JSON, Geldbeträge Zeichenketten mit Punkt (<code>&quot;1190.00&quot;</code>), Fehler <code>{"{"}&quot;detail&quot;: &quot;…&quot;{"}"}</code> mit HTTP 401/403/404/409/422.</p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-900 px-4 py-3 text-[12px] text-slate-100">{`curl -H "Authorization: Bearer sl_xxxx_…" \\\n  ${base}/api/customers`}</pre>
        </Card>
        <Card>
          <CardHeader title="Webhook-Ereignisse" description="Signatur: X-OpenEmployed-Signature: t=<Zeit>,v1=HMAC-SHA256(secret, &quot;t.body&quot;)" />
          <ul className="grid max-h-56 gap-1 overflow-y-auto text-xs sm:grid-cols-2">{Object.entries(events).map(([k, l]) => <li key={k}><code className="font-mono">{k}</code> <span className="text-slate-500">– {l}</span></li>)}</ul>
        </Card>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input type="search" aria-label="Endpunkte durchsuchen" placeholder="Endpunkt suchen, z. B. invoices oder Zahlung" value={q} onChange={(e) => setQ(e.target.value)}
          className="h-10 min-w-0 flex-1 rounded-xl border-0 bg-white px-3 text-sm shadow-card outline-none focus:shadow-[0_0_0_1px_var(--color-brand-500)] dark:bg-slate-900" />
        <select aria-label="Bereich" value={tag} onChange={(e) => setTag(e.target.value)} className="h-10 rounded-xl border-0 bg-white px-3 text-sm shadow-card dark:bg-slate-900">
          <option value="">alle Bereiche ({ops.length})</option>{tags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      <Card className="!p-0">
        <ul className="divide-y divide-slate-100 dark:divide-white/5">
          {filtered.map((o) => {
            const id = `${o.method} ${o.path}`;
            const body = o.requestBody?.content?.["application/json"]?.schema;
            const ok = Object.entries(o.responses ?? {}).find(([c]) => c.startsWith("2"));
            const resSchema = ok?.[1]?.content?.["application/json"]?.schema;
            return (
              <li key={id}>
                <button type="button" onClick={() => setOpen(open === id ? null : id)} className="flex w-full items-center gap-3 px-5 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-white/[0.03]">
                  <span className={`w-16 shrink-0 rounded-md px-1.5 py-0.5 text-center font-mono text-[11px] font-semibold uppercase ${METHOD[o.method] ?? ""}`}>{o.method}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{o.path}</span>
                  <span className="hidden truncate text-xs text-slate-500 sm:block sm:max-w-[40%]">{o.summary}</span>
                </button>
                {open === id && (
                  <div className="grid gap-4 border-t border-slate-100 bg-slate-50/50 px-5 py-4 text-sm dark:border-white/5 dark:bg-white/[0.02]">
                    {o.description && <p className="whitespace-pre-line text-slate-600 dark:text-slate-300">{o.description}</p>}
                    <div className="flex flex-wrap gap-1.5">{(o.tags ?? []).map((t) => <Badge key={t}>{t}</Badge>)}</div>
                    {(o.parameters ?? []).length > 0 && <div><p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Parameter</p>
                      <table className="mt-1 w-full text-xs"><tbody>{o.parameters!.map((p: any) => <tr key={p.in + p.name} className="border-t border-slate-100 dark:border-white/5"><td className="py-1.5 pr-3 font-mono">{p.name}{p.required && <span className="text-red-500">*</span>}</td><td className="py-1.5 pr-3 text-slate-500">{p.in}</td><td className="py-1.5 text-slate-500">{resolve(spec, p.schema).type ?? ""}</td></tr>)}</tbody></table></div>}
                    {body && <div><p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Anfrage (JSON)</p><Fields spec={spec} schema={body} />
                      <pre className="mt-2 overflow-x-auto rounded-xl bg-slate-900 px-4 py-3 text-[12px] text-slate-100">{JSON.stringify(example(spec, body), null, 2)}</pre></div>}
                    {o.requestBody?.content?.["multipart/form-data"] && <p className="text-xs text-slate-500">Anfrage als multipart/form-data (Datei-Upload).</p>}
                    <div><p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Beispielaufruf</p>
                      <pre className="mt-1 overflow-x-auto rounded-xl bg-slate-900 px-4 py-3 text-[12px] text-slate-100">{`curl -X ${o.method.toUpperCase()} "${base}${o.path}" \\\n  -H "Authorization: Bearer sl_xxxx_…"${body ? ` \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(example(spec, body))}'` : ""}`}</pre></div>
                    <div><p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Antworten</p>
                      <p className="mt-1 text-xs text-slate-500">{Object.entries(o.responses ?? {}).map(([c, r]: [string, any]) => `${c} ${r.description ?? ""}`).join(" · ")}</p>
                      {resSchema && Object.keys(resolve(spec, resSchema)).length > 0 && <pre className="mt-2 max-h-64 overflow-auto rounded-xl bg-slate-900 px-4 py-3 text-[12px] text-slate-100">{JSON.stringify(example(spec, resSchema), null, 2)}</pre>}
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}
