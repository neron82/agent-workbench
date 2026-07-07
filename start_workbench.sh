#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export WORKBENCH_ENV="${WORKBENCH_ENV:-development}"
export FLASK_APP="src.agent_workbench.web.app:create_app"

HOST="${FLASK_RUN_HOST:-0.0.0.0}"
PORT="${FLASK_RUN_PORT:-5000}"

DEBUG_ARGS=()
if [[ "$WORKBENCH_ENV" != "production" ]]; then
  DEBUG_ARGS=(--debug)
fi

# SSE / EventSource clients hold the connection open indefinitely.
# Flask's dev server is single-threaded by default, which would
# block every other request for as long as a stream is alive. In
# development we therefore opt into threaded mode. Production
# deployments should front the app with a real WSGI server
# (gunicorn -k gthread -w 1 --threads 8) instead.
THREAD_ARGS=()
if [[ "$WORKBENCH_ENV" == "development" || "$WORKBENCH_ENV" == "testing" ]]; then
  THREAD_ARGS=(--with-threads)
fi

echo "Starting Agent Workbench"
echo "  root: $ROOT_DIR"
echo "  python: $PYTHON_BIN"
echo "  env: $WORKBENCH_ENV"
echo "  bind: http://$HOST:$PORT"

action=(run --host "$HOST" --port "$PORT")
exec "$PYTHON_BIN" -m flask --app "$FLASK_APP" "${action[@]}" "${DEBUG_ARGS[@]}" "${THREAD_ARGS[@]}"
