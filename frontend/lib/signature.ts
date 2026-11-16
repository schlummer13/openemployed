// Gemeinsamer Ablauf „zur Signatur senden“ mit Formprüfung (Schriftform → Bestätigung, ausgeschlossen → Abbruch).
import { HttpError } from "@/lib/http";

type Confirm = (opts: { title: string; message?: string; confirmLabel?: string; tone?: "primary" | "danger" }) => Promise<boolean>;

export async function sendForSignature(send: (acknowledge: boolean) => Promise<unknown>, confirm: Confirm): Promise<"sent" | "cancelled"> {
  try {
    await send(false);
    return "sent";
  } catch (e) {
    const detail = e instanceof HttpError ? (e.detail as { code?: string; message?: string } | null) : null;
    if (detail?.code === "form_ack_required") {
      const ok = await confirm({
        title: "Schriftform erforderlich",
        message: `${detail.message} Documenso ersetzt die Schriftform nicht. Trotzdem senden – z. B. zusätzlich zur Papier-Unterschrift oder mit qualifizierter elektronischer Signatur?`,
        confirmLabel: "Verstanden, trotzdem senden", tone: "danger",
      });
      if (!ok) return "cancelled";
      await send(true);
      return "sent";
    }
    throw e;
  }
}
