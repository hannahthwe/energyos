#!/usr/bin/env bash
# Start ConnectedCare AI for a demo: installs what is missing, builds the web
# app, resets the demo data and starts the backend on port 8000.
#   ./start-demo.sh            fresh demo data (default)
#   ./start-demo.sh --keep     keep existing support cases
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

if [ ! -x backend/.venv/bin/uvicorn ]; then
  echo "→ Setting up Python environment (first run only)…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi

if [ ! -d web/node_modules ]; then
  echo "→ Installing web packages (first run only)…"
  (cd web && npm install --silent)
fi

if [ ! -f web/dist/index.html ] || [ -n "$(find web/src -newer web/dist/index.html -print -quit)" ]; then
  echo "→ Building the web app…"
  (cd web && npm run build >/dev/null)
fi

if [ "${1:-}" = "--keep" ]; then RESET=0; else RESET=1; fi

IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"
MODE="offline mode, no ANTHROPIC_API_KEY set"
[ -n "${ANTHROPIC_API_KEY:-}" ] && MODE="Claude mode"

cat <<EOF

  ConnectedCare AI is starting ($MODE)
  ─────────────────────────────────────────────
  Support dashboard   http://localhost:8000/support
  Homeowner app (web) http://localhost:8000/app
  API docs            http://localhost:8000/docs
EOF
[ -n "$IP" ] && cat <<EOF
  On your iPhone (same Wi-Fi):
    Safari            http://$IP:8000/app
    iOS app server    http://$IP:8000
EOF
echo "  Stop with Ctrl+C"
echo

cd "$ROOT/backend"
CONNECTEDCARE_RESET=$RESET exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
