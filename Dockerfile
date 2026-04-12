# syntax=docker/dockerfile:1.7

# ─── Stage 1: build the frontend ──────────────────────────────────────────
FROM node:22-alpine AS web-builder

WORKDIR /app/web

# Install pnpm via corepack (shipped with node:22)
RUN corepack enable && corepack prepare pnpm@latest --activate

COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

# ─── Stage 2: python runtime ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# ffmpeg is optional but needed for AC-3 → AAC transcode fallback.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install backend dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY server/ ./server/
RUN pip install --upgrade pip && pip install -e .

# Pull in the pre-built frontend bundle.
COPY --from=web-builder /app/web/dist ./web/dist

# Bring in the default channel order seed.
COPY default_names.txt ./

# Runtime data directories — declared as volumes so user data persists.
RUN mkdir -p /app/logos_cache /app/epg_cache /app/transcode_tmp
VOLUME ["/app/logos_cache", "/app/epg_cache", "/app/transcode_tmp"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/source >/dev/null || exit 1

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
