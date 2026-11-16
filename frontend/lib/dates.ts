/** Relative Datumsangaben („heute“, „in 3 Tagen“, „vor 2 Wochen“) – Kalendertage in Europe/Berlin. */

function dayNumber(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Math.round(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1) / 86_400_000);
}

export function todayBerlin(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin" }).format(new Date());
}

export function daysFromToday(iso: string, today: string = todayBerlin()): number {
  return dayNumber(iso) - dayNumber(today);
}

export function formatRelative(iso: string, today: string = todayBerlin()): string {
  const diff = daysFromToday(iso, today);
  const abs = Math.abs(diff);
  if (diff === 0) return "heute";
  if (diff === 1) return "morgen";
  if (diff === -1) return "gestern";
  const unit = abs < 14 ? [abs, abs === 1 ? "Tag" : "Tagen"] : abs < 60 ? [Math.round(abs / 7), "Wochen"] : abs < 730 ? [Math.round(abs / 30), "Monaten"] : [Math.round(abs / 365), "Jahren"];
  return diff > 0 ? `in ${unit[0]} ${unit[1]}` : `vor ${unit[0]} ${unit[1]}`;
}

/** Fälligkeit: „fällig in 3 Tagen“, „heute fällig“, „seit 5 Tagen überfällig“ samt Ton für die Darstellung */
export function dueLabel(iso: string, today: string = todayBerlin()): { text: string; tone: "red" | "amber" | "slate" } {
  const diff = daysFromToday(iso, today);
  if (diff < 0) return { text: diff === -1 ? "seit gestern überfällig" : `seit ${formatRelative(iso, today).replace("vor ", "")} überfällig`, tone: "red" };
  if (diff === 0) return { text: "heute fällig", tone: "amber" };
  if (diff <= 3) return { text: `fällig ${formatRelative(iso, today)}`, tone: "amber" };
  return { text: `fällig ${formatRelative(iso, today)}`, tone: "slate" };
}
