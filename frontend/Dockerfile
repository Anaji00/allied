# ==============================================================================
# Allied Operations frontend: Next.js standalone server on Node 22
#
# The /api/v1 rewrite to the backend is fixed when the app is built, so the
# backend address is a build argument (compose passes http://backend:8000).
# `next build` fetches the IBM Plex fonts from Google Fonts, so the build needs
# network access. `npm run api:types` is not run here: it needs Python, and
# src/lib/api/schema.d.ts is already in the source.
# ==============================================================================

FROM node:22-alpine AS base
# Some native packages (sharp for images, SWC) expect glibc symbols.
RUN apk add --no-cache libc6-compat
ENV NEXT_TELEMETRY_DISABLED=1

# --- Dependencies -------------------------------------------------------------
FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# --- Build --------------------------------------------------------------------
FROM base AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://backend:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# --- Runtime ------------------------------------------------------------------
FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S -g 1001 nodejs && adduser -S -u 1001 -G nodejs nextjs

# The standalone server needs public/ and .next/static beside server.js.
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.status < 500 ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
