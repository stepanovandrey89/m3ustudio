#!/usr/bin/env bash
# Deploy m3u Studio to the prod VPS.
#   1. rsync source tree (excluding caches + secrets)
#   2. install/update Python + npm deps
#   3. rebuild frontend bundle
#   4. fix ownership + perms (service runs as m3ustudio, NOT root)
#   5. restart the uvicorn service
#
# Notes:
#   - .env is NOT overwritten; change it on the server with ssh + sed.
#   - runtime state (state.json, plans.json, *_cache/, recordings/) is kept.
#   - pip/npm/build run as root over SSH for speed, then we chown everything
#     back to m3ustudio:m3ustudio so the sandboxed service can read it.

set -euo pipefail

# Load local (gitignored) deploy env if present — contains the production host
# so it is never committed. Create scripts/.deploy.env from .deploy.env.example.
if [ -f "$(dirname "$0")/.deploy.env" ]; then
  # shellcheck disable=SC1091
  . "$(dirname "$0")/.deploy.env"
fi

HOST=${DEPLOY_HOST:?DEPLOY_HOST is not set — create scripts/.deploy.env from .deploy.env.example}
REMOTE_DIR=${DEPLOY_DIR:-/opt/m3uplaylist}
SERVICE_USER=${DEPLOY_USER:-m3ustudio}

cd "$(dirname "$0")/.."

echo "→ syncing code to $HOST:$REMOTE_DIR"
rsync -az --delete-after \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='web/node_modules/' \
  --exclude='web/dist/' \
  --exclude='web/.vite/' \
  --exclude='__pycache__/' \
  --exclude='*.py[cod]' \
  --exclude='.ruff_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='*.egg-info/' \
  --exclude='transcode_tmp/' \
  --exclude='.DS_Store' \
  --exclude='.claude/' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='.env' \
  --exclude='/logos_cache/' \
  --exclude='/epg_cache/' \
  --exclude='/ai_cache/' \
  --exclude='/recordings/' \
  --exclude='/state.json' \
  --exclude='/plans.json' \
  ./ "$HOST:$REMOTE_DIR/"

echo "→ installing Python + npm deps on remote"
ssh "$HOST" "cd $REMOTE_DIR \
  && .venv/bin/pip install --quiet --upgrade pip wheel \
  && .venv/bin/pip install --quiet -e . \
  && cd web && npm install --no-audit --no-fund --silent && npm run build"

echo "→ fixing ownership + perms"
ssh "$HOST" "chown -R ${SERVICE_USER}:${SERVICE_USER} ${REMOTE_DIR} \
  && chmod 600 ${REMOTE_DIR}/.env \
  && find ${REMOTE_DIR} -maxdepth 1 -type d -exec chmod 755 {} \;"

echo "→ restarting m3u-studio"
ssh "$HOST" "systemctl restart m3u-studio && sleep 2 && systemctl is-active m3u-studio"

echo "→ smoke test"
curl -sk -o /dev/null -w "HTTPS=%{http_code} time=%{time_total}s\n" https://m3ustudio.ru/ || true
curl -sk -o /dev/null -w "/api/source=%{http_code}\n" https://m3ustudio.ru/api/source || true

echo "✓ deploy done"
