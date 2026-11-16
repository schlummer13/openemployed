/** OpenEmployed-Bildmarke: offener Ring (Open) mit Punkt in der Öffnung – jemand, der nach vorne geht (Employed). */
export function Logo({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className} role="img" aria-label="OpenEmployed">
      <defs>
        <linearGradient id="oe-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2bbcac" />
          <stop offset="1" stopColor="#0d6662" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#oe-g)" />
      <path d="M46.1 27.9 A15 15 0 1 1 34.6 18.2" fill="none" stroke="#fff" strokeWidth="6.5" strokeLinecap="round" />
      <circle cx="45" cy="16.5" r="4.2" fill="#a7f3d0" />
    </svg>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  return <span className={`font-semibold tracking-[-0.02em] ${className}`}>Open<span className="text-brand-600 dark:text-brand-400">Employed</span></span>;
}
