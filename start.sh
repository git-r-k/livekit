#!/usr/bin/env bash
# Production start script for Render.
#
# Runs both the LiveKit agent worker (background) and the Flask UI (foreground).
# The Flask process binds to $PORT so Render keeps the service alive.

set -e

echo "==> starting LiveKit agent worker"
python agent.py start &
AGENT_PID=$!

trap "echo '==> stopping agent'; kill $AGENT_PID 2>/dev/null || true" EXIT INT TERM

echo "==> starting Flask UI on :$PORT"
exec gunicorn server:app \
  --bind "0.0.0.0:${PORT:-5001}" \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
