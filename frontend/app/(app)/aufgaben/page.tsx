"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Avatar } from "@/components/Avatar";
import { Icon, type IconName } from "@/components/Icon";
import { RelDate } from "@/components/RelDate";
import { Badge, Card, Empty, PageHeader, PageSkeleton } from "@/components/ui";
import { api } from "@/lib/http";

interface Task { severity: "red" | "amber" | "blue"; title: string; href: string; detail: string | null; count?: number | null }
interface ProjectTask {
  id: string; title: string; priority: "dringend" | "hoch" | "mittel" | "niedrig"; due_date: string | null; project_id: string; project_name: string;
  project_color: string; assignee_id: string | null; assignee_name: string | null; assignee_color: string | null; assignee_photo: boolean;
}

const AREAS: { prefix: string; label: string; icon: IconName }[] = [
  { prefix: "/belege", label: "Belege & Ausgaben", icon: "receipt" },
  { prefix: "/buchhaltung", label: "Buchhaltung & Bank", icon: "bank" },
  { prefix: "/forderungen", label: "Forderungen", icon: "alarm" },
  { prefix: "/rechnungen", label: "Rechnungen", icon: "file" },
  { prefix: "/angebote", label: "Angebote", icon: "send" },
  { prefix: "/vertraege", label: "Verträge", icon: "repeat" },
  { prefix: "/steuern", label: "Steuern", icon: "percent" },
  { prefix: "/budgets", label: "Budgets", icon: "pie" },
  { prefix: "/gehaelter", label: "Team & Gehälter", icon: "wallet" },
  { prefix: "/", label: "Allgemein", icon: "zap" },
];
const DOT = { red: "bg-red-500", amber: "bg-amber-500", blue: "bg-brand-500" };
const ORDER = { red: 0, amber: 1, blue: 2 };
const PRIO = { dringend: "red", hoch: "amber", mittel: "blue", niedrig: "slate" } as const;

export default function InboxPage() {
  const [data, setData] = useState<{ tasks: Task[]; project_tasks: ProjectTask[] } | null>(null);
  useEffect(() => { void api<{ tasks: Task[]; project_tasks: ProjectTask[] }>("GET", "/api/inbox").then(setData); }, []);
  const groups = useMemo(() => {
    const out = AREAS.map((a) => ({ ...a, tasks: [] as Task[] }));
    for (const t of data?.tasks ?? []) {
      const g = out.find((a) => (a.prefix === "/" ? true : t.href.startsWith(a.prefix)));
      g?.tasks.push(t);
    }
    return out.filter((g) => g.tasks.length).map((g) => ({ ...g, tasks: g.tasks.sort((x, y) => ORDER[x.severity] - ORDER[y.severity]) }));
  }, [data]);
  if (!data) return <PageSkeleton stats={0} />;
  const urgent = data.tasks.filter((t) => t.severity === "red").length;

  return (
    <>
      <PageHeader title="Zu erledigen" description="Alles Offene an einem Ort – automatisch aus Belegen, Forderungen, Fristen und Projekten ermittelt." />
      <div className="mb-6 flex flex-wrap gap-2">
        <Badge tone={urgent ? "red" : "green"}>{urgent} dringend</Badge>
        <Badge tone="amber">{data.tasks.length - urgent} weitere Punkte</Badge>
        <Badge tone="blue">{data.project_tasks.length} Projektaufgaben</Badge>
      </div>

      {groups.length === 0 && data.project_tasks.length === 0 ? <Card><Empty>Alles erledigt – nichts offen.</Empty></Card> : (
        <div className="grid gap-6 xl:grid-cols-2">
          <div className="space-y-6">
            {groups.map((g) => (
              <Card key={g.prefix} className="!p-0">
                <h2 className="flex items-center gap-2 border-b border-slate-100 px-5 py-3.5 font-semibold dark:border-white/5">
                  <Icon name={g.icon} className="size-4 text-slate-400" />{g.label}
                  <span className="ml-auto text-xs font-normal text-slate-500">{g.tasks.length}</span>
                </h2>
                <ul className="divide-y divide-slate-100 dark:divide-white/5">
                  {g.tasks.map((t) => (
                    <li key={t.title}>
                      <Link href={t.href} className="group flex items-start gap-3 px-5 py-3 transition hover:bg-slate-25 dark:hover:bg-white/[0.02]">
                        <span className={`mt-1.5 size-2 shrink-0 rounded-full ${DOT[t.severity]}`} />
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium">{t.title}</span>
                          {t.detail && <span className="block text-xs text-slate-500">{t.detail}</span>}
                        </span>
                        <Icon name="arrow" className="mt-0.5 size-4 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-500" />
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
          <Card className="!p-0 self-start">
            <h2 className="flex items-center gap-2 border-b border-slate-100 px-5 py-3.5 font-semibold dark:border-white/5">
              <Icon name="kanban" className="size-4 text-slate-400" />Projektaufgaben
              <Link href="/projekte" className="ml-auto text-xs font-medium text-brand-700 hover:underline dark:text-brand-300">Zu den Projekten</Link>
            </h2>
            {data.project_tasks.length === 0 ? <p className="px-5 py-6 text-sm text-slate-500">Keine offenen Aufgaben.</p> : (
              <ul className="divide-y divide-slate-100 dark:divide-white/5">
                {data.project_tasks.map((t) => (
                  <li key={t.id} className="flex items-center gap-3 px-5 py-2.5">
                    <Avatar person={t.assignee_id ? { id: t.assignee_id, name: t.assignee_name ?? "", color: t.assignee_color, has_photo: t.assignee_photo } : null} size={26} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{t.title}</span>
                      <span className="flex flex-wrap items-center gap-x-1.5 text-xs text-slate-500">
                        <span className="size-2 rounded-full" style={{ background: t.project_color }} />{t.project_name}
                        {t.due_date && <> · <RelDate date={t.due_date} mode="due" /></>}
                      </span>
                    </span>
                    <Badge tone={PRIO[t.priority]}>{t.priority}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
