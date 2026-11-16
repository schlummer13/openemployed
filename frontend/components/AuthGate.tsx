"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getMe, type Me } from "@/lib/api";

const UserContext = createContext<Me | null>(null);

export function useUser(): Me | null {
  return useContext(UserContext);
}

/** Zeigt die App nur angemeldeten Benutzern; sonst Weiterleitung zur Anmeldung. Rechte prüft das Backend je Rolle. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    getMe()
      .then((me) => {
        if (!active) return;
        if (me) {
          // Mitarbeiter sehen keine Finanzübersicht – Start in den Projekten
          if ((me.permissions?.dashboard ?? "write") === "none" && window.location.pathname === "/") window.location.replace("/stempeluhr");
          setUser(me);
        }
        else {
          const next = window.location.pathname + window.location.search;
          window.location.replace(`/login?next=${encodeURIComponent(next)}`);
        }
      })
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, []);

  if (failed) {
    return (
      <div className="grid min-h-screen place-items-center p-6 text-sm text-slate-500">
        Backend nicht erreichbar. Bitte später erneut versuchen.
      </div>
    );
  }
  if (!user) {
    return <div className="grid min-h-screen place-items-center text-sm text-slate-400">Anmeldung wird geprüft …</div>;
  }
  return <UserContext.Provider value={user}>{children}</UserContext.Provider>;
}
