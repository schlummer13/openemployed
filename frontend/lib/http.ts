// Generischer API-Zugriff: Fehler als ApiError mit deutscher Meldung, 401 → Anmeldung.

export class HttpError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown = null,
  ) {
    super(message);
  }
}

function messageFrom(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail && typeof (detail as { message: unknown }).message === "string") {
    return (detail as { message: string }).message;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((d: { msg?: string; loc?: (string | number)[] }) => {
        const field = d.loc?.filter((x) => x !== "body").join(" → ");
        const msg = (d.msg ?? "").replace(/^Value error, /, "");
        return field ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  return `Fehler ${status}`;
}

/** Wie ``api``, aber ohne Weiterleitung zur internen Anmeldung (Kundenportal, öffentliche Seiten). */
export async function publicApi<T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    cache: "no-store",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new HttpError(messageFrom(data, res.status), res.status, (data as { detail?: unknown } | null)?.detail ?? null);
  return data as T;
}

/** Lesende Anfragen werden bei Netzfehlern bzw. 502/503/504 (z. B. Neustart des Servers) einmal kurz wiederholt.
 *  Schreibende Anfragen nie automatisch – sonst droht eine doppelte Buchung. */
async function fetchWithRetry(path: string, init: RequestInit, retry: boolean): Promise<Response> {
  try {
    const res = await fetch(path, init);
    if (retry && (res.status === 502 || res.status === 503 || res.status === 504)) {
      await new Promise((r) => setTimeout(r, 400));
      return fetch(path, init);
    }
    return res;
  } catch (err) {
    if (!retry) throw err;
    await new Promise((r) => setTimeout(r, 400));
    return fetch(path, init);
  }
}

export async function api<T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetchWithRetry(path, {
    method,
    cache: "no-store",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }, method === "GET");
  if (res.status === 401) {
    const next = window.location.pathname + window.location.search;
    window.location.replace(`/login?next=${encodeURIComponent(next)}`);
    throw new HttpError("Nicht angemeldet", 401);
  }
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new HttpError(messageFrom(data, res.status), res.status, (data as { detail?: unknown } | null)?.detail ?? null);
  return data as T;
}

/** Datei-Upload (multipart) mit zusätzlichen Formularfeldern, gleiche Fehlerbehandlung wie api(). */
export async function upload<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  const res = await fetch(path, { method: "POST", body: fd, cache: "no-store" });
  if (res.status === 401) {
    window.location.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
    throw new HttpError("Nicht angemeldet", 401);
  }
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new HttpError(messageFrom(data, res.status), res.status, (data as { detail?: unknown } | null)?.detail ?? null);
  return data as T;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

export function todayIso(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}
