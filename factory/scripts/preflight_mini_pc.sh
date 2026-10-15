#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${1:-/opt/agentfactory}"
ENV_FILE="${2:-$DEPLOY_ROOT/.env.lan}"
FACTORY_DIR="$DEPLOY_ROOT/factory"
DB_PATH="${FACTORY_DB_PATH:-$FACTORY_DIR/database/factory.db}"
WORKSPACE_DIR="${FACTORY_WORKSPACE_DIR:-$FACTORY_DIR/workspace}"
API_PORT="${FACTORY_API_PORT:-8180}"

fail() {
  echo "[FAIL] $1"
  exit 1
}

ok() {
  echo "[OK] $1"
}

[[ -d "$DEPLOY_ROOT" ]] || fail "deploy root not found: $DEPLOY_ROOT"
[[ -d "$FACTORY_DIR" ]] || fail "factory directory not found: $FACTORY_DIR"
[[ -f "$ENV_FILE" ]] || fail "env file not found: $ENV_FILE"

# shellcheck disable=SC1090
source "$ENV_FILE"
[[ -n "${FACTORY_API_KEY:-}" && "${FACTORY_API_KEY}" != "change_me" ]] || fail "FACTORY_API_KEY missing or change_me in $ENV_FILE"
ok "env file and FACTORY_API_KEY"

for cmd in python3 sqlite3 curl ss claude; do
  command -v "$cmd" >/dev/null 2>&1 || fail "command not found: $cmd"
done
ok "required commands present (python3/sqlite3/curl/ss/claude)"

mkdir -p "$(dirname "$DB_PATH")" "$WORKSPACE_DIR"
touch "$DB_PATH" || fail "cannot write DB file: $DB_PATH"
[[ -w "$DB_PATH" ]] || fail "DB file is not writable: $DB_PATH"
[[ -w "$WORKSPACE_DIR" ]] || fail "workspace directory is not writable: $WORKSPACE_DIR"
sqlite3 "$DB_PATH" 'SELECT 1;' >/dev/null || fail "sqlite DB check failed: $DB_PATH"
ok "DB/workspace permissions"

if ss -ltn "sport = :$API_PORT" | grep -q LISTEN; then
  echo "[WARN] API port $API_PORT already in use"
else
  ok "API port $API_PORT is free"
fi

claude --version >/dev/null 2>&1 || fail "claude command exists but is not functional"
ok "claude CLI available"

api_ready_code=$(curl -s -o /tmp/agentfactory_preflight_api_ready.json -w "%{http_code}" "http://127.0.0.1:${API_PORT}/ready" || true)

if [[ "$api_ready_code" == "200" ]]; then
  ok "/ready checks passed (api=$api_ready_code)"
else
  fail "/ready checks failed (api=$api_ready_code). Start services first: sudo ./factory/scripts/install_systemd.sh"
fi
