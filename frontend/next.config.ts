import type { NextConfig } from "next";

// Produktion: nginx leitet /api/* an das Backend weiter (lokal: Caddy, Profil "lokal").
// Für `npm run dev` ohne Proxy: BACKEND_URL=http://localhost:8000 setzen.
const backendUrl = process.env.BACKEND_URL;

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return backendUrl ? [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }] : [];
  },
};

export default nextConfig;
