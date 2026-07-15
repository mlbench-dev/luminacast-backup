# Remotion SSR caption-render service.
#
# Build context is the REPO ROOT (see docker-compose.yml: context: .) so the
# image can copy the two frontend caption sources at build time, keeping a
# single source of truth — the originals live in
# frontend/companion-app/src and are NEVER edited inside this service.
#
# Remotion renders headless Chromium, so we use the Remotion-maintained
# Node 20 base that ships the required Chromium system libraries.
FROM node:20-bookworm-slim

# Chromium runtime libraries Remotion's bundled browser needs. Installed
# explicitly so the slim base can launch headless Chrome for the render.
RUN apt-get update && apt-get install -y --no-install-recommends \
	ca-certificates \
	fonts-liberation \
	libnss3 \
	libdbus-1-3 \
	libatk1.0-0 \
	libatk-bridge2.0-0 \
	libcups2 \
	libdrm2 \
	libxkbcommon0 \
	libxcomposite1 \
	libxdamage1 \
	libxfixes3 \
	libxrandr2 \
	libgbm1 \
	libasound2 \
	libpango-1.0-0 \
	libcairo2 \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install JS deps first for layer caching. Use npm install (not ci) so a
# lockfile-less first build still resolves; the merge train regenerates the
# lockfile in place.
COPY captions-ssr/package.json ./
RUN npm install --no-audit --no-fund

# Service source.
COPY captions-ssr/tsconfig.json ./
COPY captions-ssr/src ./src

# Single source of truth: copy the EXACT frontend caption preset table and
# per-token animation component into the mirror the composition imports.
# These two files are .gitignore'd inside captions-ssr — they only ever
# exist here as build-time copies, never edited in two places.
COPY frontend/companion-app/src/lib/captionPresets.ts \
	./src/frontend-mirror/captionPresets.ts
COPY frontend/companion-app/src/components/cast-builder/editor-starter/items/captions/caption-page.tsx \
	./src/frontend-mirror/items/captions/caption-page.tsx

# Let Remotion know Chromium's shared libs are present (skips its own
# download path that needs extra privileges).
ENV REMOTION_CHROME_MODE=chrome-for-testing

ENV CAPTIONS_SSR_PORT=3030
EXPOSE 3030

# tsx runs the TS server directly; no separate compile step.
CMD ["npx", "tsx", "./src/server.ts"]
