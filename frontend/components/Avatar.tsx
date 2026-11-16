export interface Person { id: string; name: string; color?: string | null; has_photo?: boolean | null }

export function Avatar({ person, size = 28, version }: { person: Person | null | undefined; size?: number; version?: string }) {
  if (!person) return <span className="inline-grid shrink-0 place-items-center rounded-full border border-dashed border-slate-300 text-[10px] text-slate-400 dark:border-slate-600" style={{ width: size, height: size }} title="nicht zugeordnet">–</span>;
  const initials = person.name.split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("");
  return person.has_photo ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={`/api/shareholders/${person.id}/photo${version ? `?v=${encodeURIComponent(version)}` : ""}`} alt={person.name} title={person.name}
      className="shrink-0 rounded-full object-cover ring-2 ring-white dark:ring-slate-900" style={{ width: size, height: size }} />
  ) : (
    <span title={person.name} className="inline-grid shrink-0 place-items-center rounded-full font-semibold text-white ring-2 ring-white dark:ring-slate-900"
      style={{ width: size, height: size, background: person.color ?? "#6366f1", fontSize: Math.max(10, size * 0.38) }}>{initials}</span>
  );
}
