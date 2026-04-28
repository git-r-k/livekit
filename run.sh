#!/usr/bin/env bash
# Run the LiveKit voice assistant.
#
# Usage:
#   ./run.sh              # start agent worker + Flask UI together
#   ./run.sh agent        # only the agent worker
#   ./run.sh server       # only the Flask UI / token server
#   ./run.sh console      # talk to the agent in your terminal (no room)

set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "==> creating venv"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

source .venv/bin/activate

mode="${1:-all}"

case "$mode" in
  agent)
    exec python agent.py dev
    ;;
  server)
    exec python server.py
    ;;
  console)
    exec python agent.py console
    ;;
  all)
    echo "==> starting agent worker (logs: agent.log)"
    python agent.py dev > agent.log 2>&1 &
    AGENT_PID=$!
    trap "echo '==> stopping'; kill $AGENT_PID 2>/dev/null || true" EXIT INT TERM
    echo "==> starting Flask UI on http://localhost:${PORT:-5000}"
    python server.py
    ;;
  *)
    echo "unknown mode: $mode"
    echo "usage: $0 [agent|server|console|all]"
    exit 1
    ;;
esac
