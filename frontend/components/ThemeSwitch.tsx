"use client";

import { useEffect, useState } from "react";
import { Icon, type IconName } from "./Icon";

type Theme = "system" | "light" | "dark";
const OPTIONS: { value: Theme; label: string; icon: IconName }[] = [
  { value: "system", label: "Wie System", icon: "monitor" },
  { value: "light", label: "Hell", icon: "sun" },
  { value: "dark", label: "Dunkel", icon: "moon" },
];

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Farbschema: System, Hell oder Dunkel (gespeichert je Browser). */
export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    try { setTheme((localStorage.getItem("theme") as Theme | null) ?? "system"); } catch { /* ohne Speicher */ }
  }, []);
  useEffect(() => {
    apply(theme);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);
  const choose = (t: Theme) => {
    setTheme(t);
    try { localStorage.setItem("theme", t); } catch { /* ohne Speicher */ }
  };
  return (
    <div role="radiogroup" aria-label="Farbschema" className="flex rounded-lg bg-slate-100 p-0.5 dark:bg-white/5">
      {OPTIONS.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={theme === o.value} aria-label={o.label} title={o.label} onClick={() => choose(o.value)}
          className={`grid h-7 flex-1 place-items-center rounded-md transition ${theme === o.value ? "bg-white text-slate-900 shadow-card dark:bg-white/10 dark:text-white" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}>
          <Icon name={o.icon} className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
