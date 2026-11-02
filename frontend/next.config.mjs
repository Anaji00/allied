/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // Pin the project root to this folder. Next otherwise takes the nearest
  // lockfile's folder, and a stray one in the user's home made the dev server
  // watch everything there and reload on every edit anywhere in allied3.
  turbopack: { root: import.meta.dirname },
  outputFileTracingRoot: import.meta.dirname,
  // Memoizes components automatically, so pages don't need hand-written useMemo/useCallback.
  reactCompiler: true,
  // Dev only: lets a phone test the dev server through a Tailscale address (pc.tailnet.ts.net).
  allowedDevOrigins: ['**.ts.net'],
  experimental: {
    // The /api rewrite proxy gives up after 30s by default, which cut off AI
    // requests to the local model (35-60s each; a drawing check reads two).
    proxyTimeout: 300_000,
  },
  // Phones must pick up a new service worker on the next visit, never a cached copy.
  async headers() {
    return [
      {
        source: '/sw.js',
        headers: [
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
          { key: 'Content-Security-Policy', value: "default-src 'self'; script-src 'self'" },
        ],
      },
    ];
  },
  // Pages folded into others; old bookmarks and alert links still land.
  async redirects() {
    return [
      { source: '/ai', destination: '/chat?tab=translate', permanent: false },
      { source: '/documents', destination: '/chat?tab=documents', permanent: false },
      { source: '/automation', destination: '/?automate=true', permanent: false },
      { source: '/boards', destination: '/', permanent: false },
      { source: '/integrations', destination: '/health?tab=monday', permanent: false },
    ];
  },
  async rewrites() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
    return [
      {
        source: '/api/v1/:path*',
        destination: `${apiUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
