import type { MetadataRoute } from "next";

/** Installierbare Web-App (Handy): Start direkt in der Beleg-Erfassung. Kein Offline-Speicher für Finanzdaten. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "OpenEmployed – Finanzen, Lohn & Steuern",
    short_name: "OpenEmployed",
    description: "Belege fotografieren, Rechnungen, Buchhaltung und Steuern der GmbH/UG",
    start_url: "/erfassen",
    scope: "/",
    display: "standalone",
    background_color: "#f7f7f9",
    theme_color: "#0b8078",
    lang: "de",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [{ name: "Beleg fotografieren", url: "/erfassen" }, { name: "Zu erledigen", url: "/aufgaben" }],
  };
}
