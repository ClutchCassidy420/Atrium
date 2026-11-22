#!/usr/bin/env bash
# Start an isolated, loopback-only source build. Bash 5.1+.
set -euo pipefail
umask 077
: "${ATRIUM_STATE_DIR:?set a private development state directory}"
: "${ATRIUM_RUNTIME_PYTHON:?set the absolute path to the runtime Python}"
: "${ATRIUM_WEB_PYTHON:?set the absolute path to the frontend Python}"
: "${API_SERVER_KEY:?set the development gateway key}"
: "${HERMES_DASHBOARD_SESSION_TOKEN:?set the development dashboard token}"
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$ATRIUM_STATE_DIR"
state=$(cd "$ATRIUM_STATE_DIR" && pwd)
if [[ ! -O $state || $(stat -c %a "$state") != 700 ]]; then
    echo "Use a state directory owned by your account with mode 0700." >&2
    exit 1
fi
export HERMES_HOME="$state/runtime"
export DATA_DIR="$state/web-data"
export INSTANCE_DIR="${INSTANCE_DIR:-$root/atrium/instance.example}"
mkdir -p "$HERMES_HOME" "$DATA_DIR"
export API_SERVER_ENABLED=true API_SERVER_HOST=127.0.0.1 API_SERVER_PORT=18645
export HERMES_API_BASE=http://127.0.0.1:18645 HERMES_API_KEY="$API_SERVER_KEY"
export HERMES_DASHBOARD_BASE=http://127.0.0.1:18644 HERMES_DASHBOARD_TOKEN="$HERMES_DASHBOARD_SESSION_TOKEN"
export HERMES_DATA_DIR="$HERMES_HOME"
children=()
cleanup() {
    if (( ${#children[@]} )); then
        kill "${children[@]}" 2>/dev/null || true
        wait "${children[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cd "$root"
"$ATRIUM_RUNTIME_PYTHON" -m uvicorn hermes_cli.web_server:app --host 127.0.0.1 --port 18644 &
children+=("$!")
"$ATRIUM_RUNTIME_PYTHON" -m hermes_cli.main gateway run &
children+=("$!")
cd atrium/app
"$ATRIUM_WEB_PYTHON" -m uvicorn run:app --host 127.0.0.1 --port 18648 &
children+=("$!")
wait -n "${children[@]}"
