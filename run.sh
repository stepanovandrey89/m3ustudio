#!/usr/bin/env bash
# m3u Studio local dev runner: starts FastAPI + Vite concurrently.
# Ctrl-C stops both.

set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ creating Python venv"
  python3.12 -m venv .venv
fi
echo "→ syncing Python deps"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .

if [ ! -d web/node_modules ]; then
  echo "→ installing web deps"
  (cd web && pnpm install)
fi

LAN=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "0.0.0.0")

echo "→ starting backend  0.0.0.0:8000"
.venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload &
BACK=$!

# Wait until backend is accepting connections (max 15s)
for i in $(seq 1 15); do
  curl -sf http://127.0.0.1:8000/api/source > /dev/null 2>&1 && break
  sleep 1
done

echo "→ starting frontend $LAN:5173"
(cd web && pnpm dev --host "$LAN" --port 5173) &
FRONT=$!

cleanup() {
  echo
  echo "→ stopping"
  kill $BACK $FRONT 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo
echo "  ⚡ m3u Studio ready — open http://$LAN:5173"
echo

wait
