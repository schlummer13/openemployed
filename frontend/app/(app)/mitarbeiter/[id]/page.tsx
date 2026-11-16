"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { EmployeeChecklists, PersonnelFile } from "@/components/Personnel";
import { TimeAccount } from "@/components/TimeAccount";
import { Alert, Badge, PageHeader, PageSkeleton, Tabs } from "@/components/ui";
import { api, formatDate, HttpError } from "@/lib/http";

interface Employee { id: string; number: number; first_name: string; last_name: string; email: string | null; position: string | null; entry_date: string; exit_date: string | null; employment_type: string }
type Tab = "time" | "checklists" | "file";

export default function EmployeeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [emp, setEmp] = useState<Employee | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("time");
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t === "checklists" || t === "file") setTab(t);
    api<{ employees: Employee[] }>("GET", "/api/payroll/employees").then((r) => {
      const e = r.employees.find((x) => x.id === id);
      if (e) setEmp(e); else setError("Mitarbeiter nicht gefunden");
    }).catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, [id]);
  if (error) return <Alert>{error}</Alert>;
  if (!emp) return <PageSkeleton stats={3} />;
  const kinds: Record<string, string> = { regular: "Angestellt", minijob: "Minijob", werkstudent: "Werkstudent/in" };
  return (
    <div className="space-y-6">
      <Link href="/mitarbeiter" className="-mb-4 inline-block text-sm text-slate-500 hover:text-slate-900 dark:hover:text-white">← Mitarbeiter & Lohn</Link>
      <PageHeader eyebrow="Personal"
        title={`${emp.first_name} ${emp.last_name}`}
        description={`Nr. ${String(emp.number).padStart(3, "0")} · ${emp.position || kinds[emp.employment_type] || ""} · seit ${formatDate(emp.entry_date)}${emp.exit_date ? ` · Austritt ${formatDate(emp.exit_date)}` : ""}`}
        actions={emp.exit_date ? <Badge tone="amber">Austritt geplant</Badge> : undefined} />
      <Tabs value={tab} onChange={setTab} items={[{ value: "time", label: "Zeitkonto" }, { value: "checklists", label: "On-/Offboarding" }, { value: "file", label: "Personalakte" }]} />
      {tab === "time" && <TimeAccount path={`/api/personnel/${emp.id}/time-account`} fetcher={api} manage employeeId={emp.id} />}
      {tab === "checklists" && <EmployeeChecklists employeeId={emp.id} entryDate={emp.entry_date} exitDate={emp.exit_date} />}
      {tab === "file" && <PersonnelFile employeeId={emp.id} email={emp.email} employeeName={`${emp.first_name} ${emp.last_name}`} />}
    </div>
  );
}
