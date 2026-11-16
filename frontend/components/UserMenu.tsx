"use client";

import { useState } from "react";
import { logout } from "@/lib/api";
import { useUser } from "./AuthGate";
import { Icon } from "./Icon";
import { ThemeSwitch } from "./ThemeSwitch";

export function UserMenu() {
  const user = useUser();
  const [busy, setBusy] = useState(false);
  if (!user) return null;

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
    } finally {
      window.location.replace("/login");
    }
  }

  const initials = (user.name ?? user.email).split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("");
  return (
    <div className="space-y-2">
    <ThemeSwitch />
    <div className="flex items-center gap-2.5 rounded-xl bg-white p-2 text-sm shadow-card dark:bg-white/[0.04] dark:shadow-none">
      {user.picture ? <img src={user.picture} alt="" className="size-8 rounded-full" referrerPolicy="no-referrer" />
        : <span className="grid size-8 shrink-0 place-items-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-900 dark:text-brand-100">{initials}</span>}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium" title={user.email}>{user.name ?? user.email}</div>
        <div className="truncate text-xs text-slate-500">{user.email}</div>
      </div>
      <button type="button" onClick={onLogout} disabled={busy} aria-label="Abmelden" title="Abmelden"
        className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-200 hover:text-slate-900 disabled:opacity-60 dark:hover:bg-slate-800 dark:hover:text-white">
        <Icon name="logout" />
      </button>
    </div>
    </div>
  );
}
