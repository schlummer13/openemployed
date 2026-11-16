// Typen spiegeln die Backend-Schemas; Beträge kommen immer als String (Decimal), nie als Zahl.

export type PriceMode = "net" | "gross";

export interface LineIn {
  description: string;
  quantity: string;
  unit_code: string;
  unit_price: string;
  tax_case: string;
}

export interface LineOut {
  position: number;
  description: string;
  quantity: string;
  unit_code: string;
  unit_price_net: string;
  unit_price_gross: string | null;
  line_net: string;
  line_gross: string | null;
  tax_case: string;
  category: string;
  rate: string;
}

export interface TaxBreakdownOut {
  category: string;
  rate: string;
  taxable_amount: string;
  tax_amount: string;
  tax_cases: string[];
}

export interface CalculateResponse {
  price_mode: PriceMode;
  lines: LineOut[];
  tax_breakdown: TaxBreakdownOut[];
  total_net: string;
  total_tax: string;
  total_gross: string;
  gross_target: string | null;
  gross_deviation: string;
  warnings: string[];
}

export interface HealthResponse {
  status: "ok" | "degraded";
  checks: Record<string, boolean>;
}

export class ApiError extends Error {}

export type Role = "admin" | "accounting" | "advisor" | "employee";
export interface Me {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
  role: string;
  role_label?: string;
  permissions?: Record<string, "none" | "read" | "write">;
  auth_provider?: string;
  totp_enabled?: boolean;
  has_password?: boolean;
  needs_2fa?: boolean;
}

export interface AuthConfig {
  google_configured: boolean;
  microsoft_configured?: boolean;
  oidc_configured?: boolean;
  oidc_label?: string;
  password_enabled?: boolean;
  domain: string | null;
  dev_login?: boolean;
}

/** 401 → zur Anmeldung (Sitzung abgelaufen). */
function redirectToLogin(): never {
  const next = window.location.pathname + window.location.search;
  window.location.replace(`/login?next=${encodeURIComponent(next)}`);
  throw new ApiError("Nicht angemeldet");
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 401) redirectToLogin();
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg ?? "").join("; ")
          : `Fehler ${res.status}`;
    throw new ApiError(message);
  }
  return body as T;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/health", { cache: "no-store" });
  // 503 enthält ebenfalls einen gültigen Status-Body
  return (await res.json()) as HealthResponse;
}

export async function getMe(): Promise<Me | null> {
  const res = await fetch("/api/auth/me", { cache: "no-store" });
  if (res.status === 401) return null;
  if (!res.ok) throw new ApiError(`Fehler ${res.status}`);
  return (await res.json()) as Me;
}

export async function getAuthConfig(): Promise<AuthConfig> {
  const res = await fetch("/api/auth/config", { cache: "no-store" });
  if (!res.ok) throw new ApiError(`Fehler ${res.status}`);
  return (await res.json()) as AuthConfig;
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
}

export async function calculateInvoice(price_mode: PriceMode, lines: LineIn[]): Promise<CalculateResponse> {
  const res = await fetch("/api/invoices/calculate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ price_mode, lines }),
  });
  return parse<CalculateResponse>(res);
}

/** Formatiert einen Decimal-String deutsch, ohne Umweg über float (keine Rundungsfehler). */
export function formatEuro(input: string | number | null | undefined): string {
  if (input == null || input === "") return "–";
  const value = typeof input === "number" ? input.toFixed(2) : String(input);
  const negative = value.startsWith("-");
  const [intPart = "0", fracPart = ""] = (negative ? value.slice(1) : value).split(".");
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const frac = (fracPart + "00").slice(0, Math.max(2, fracPart.replace(/0+$/, "").length));
  return `${negative ? "−" : ""}${grouped},${frac}\u00a0€`;
}

/** Wandelt deutsche Eingabe ("1.758,50") in API-Format ("1758.50"). */
export function toApiDecimal(input: string): string {
  const t = input.trim().replace(/\s/g, "");
  if (t.includes(",")) return t.replace(/\./g, "").replace(",", ".");
  return t;
}
