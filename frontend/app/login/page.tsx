import { Suspense } from "react";
import { LoginCard } from "@/components/LoginCard";
import { Logo, Wordmark } from "@/components/Logo";

export const metadata = { title: "Anmelden · OpenEmployed" };

export default function LoginPage() {
  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden px-4">
      <div aria-hidden className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[880px] -translate-x-1/2 rounded-full bg-gradient-to-br from-brand-400/30 via-emerald-300/20 to-transparent blur-3xl dark:from-brand-600/25 dark:via-emerald-800/15" />
      <div className="relative w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <Logo size={48} className="drop-shadow-md" />
          <div>
            <h1 className="text-xl"><Wordmark /></h1>
            <p className="text-sm text-slate-500">Open-Source-Software für Selbstständige und kleine Firmen</p>
          </div>
        </div>
        <Suspense>
          <LoginCard />
        </Suspense>
      </div>
    </div>
  );
}
