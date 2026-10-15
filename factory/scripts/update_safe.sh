#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${1:-/opt/agentfactory}"
ENV_FILE="${2:-$DEPLOY_ROOT/.env.lan}"
FACTORY_DIR="$DEPLOY_ROOT/factory"
REPO_DIR="$DEPLOY_ROOT"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "[FAIL] Git repository not found: $REPO_DIR"
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

API_PORT="${FACTORY_API_PORT:-8180}"
UPDATE_REMOTE="${FACTORY_UPDATE_REMOTE:-origin}"
UPDATE_BRANCH="${FACTORY_UPDATE_BRANCH:-OpenClaw}"
API_KEY="${FACTORY_API_KEY:-}"
BACKUP_SCRIPT="$FACTORY_DIR/scripts/backup_create.sh"
RESTORE_SCRIPT="$FACTORY_DIR/scripts/backup_restore.sh"

LATEST_BACKUP_PATH=""
PREV_COMMIT=""

rollback() {
  echo "[ROLLBACK] Update failed, starting rollback..."

  if [[ -n "$PREV_COMMIT" ]]; then
    (cd "$REPO_DIR" && git reset --hard "$PREV_COMMIT") || true
  fi

  if [[ -n "$LATEST_BACKUP_PATH" && -f "$LATEST_BACKUP_PATH" ]]; then
    bash "$RESTORE_SCRIPT" "$DEPLOY_ROOT" "$LATEST_BACKUP_PATH" "$ENV_FILE" --yes || true
  fi

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart agentfactory-api.service 2>/dev/null || true
  fi

  echo "[ROLLBACK] Completed. Investigate logs before next update attempt."
}

trap 'rollback' ERR

if [[ ! -x "$BACKUP_SCRIPT" || ! -x "$RESTORE_SCRIPT" ]]; then
  echo "[FAIL] Required scripts are missing or not executable:"
  echo "  $BACKUP_SCRIPT"
  echo "  $RESTORE_SCRIPT"
  exit 1
fi

if [[ -z "$API_KEY" || "$API_KEY" == "change_me" ]]; then
  echo "[FAIL] FACTORY_API_KEY is missing in $ENV_FILE"
  exit 1
fi

PREV_COMMIT="$(cd "$REPO_DIR" && git rev-parse HEAD)"
echo "[INFO] Current commit: $PREV_COMMIT"

BACKUP_OUTPUT="$(bash "$BACKUP_SCRIPT" "$DEPLOY_ROOT" "$ENV_FILE")"
echo "$BACKUP_OUTPUT"
LATEST_BACKUP_PATH="$(echo "$BACKUP_OUTPUT" | sed -n 's/^\[OK\] Backup created: //p' | tail -n 1)"
if [[ -z "$LATEST_BACKUP_PATH" ]]; then
  echo "[FAIL] Could not resolve backup path from backup output"
  exit 1
fi

echo "[INFO] Fetching updates from $UPDATE_REMOTE/$UPDATE_BRANCH"
(cd "$REPO_DIR" && git fetch "$UPDATE_REMOTE" "$UPDATE_BRANCH")
(cd "$REPO_DIR" && git checkout "$UPDATE_BRANCH")
(cd "$REPO_DIR" && git pull --ff-only "$UPDATE_REMOTE" "$UPDATE_BRANCH")

NEW_COMMIT="$(cd "$REPO_DIR" && git rev-parse HEAD)"
echo "[INFO] Updated to commit: $NEW_COMMIT"

if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart agentfactory-api.service
fi

api_ready_code="$(curl -s -o /tmp/agentfactory_update_api_ready.json -w "%{http_code}" "http://127.0.0.1:${API_PORT}/ready")"

if [[ "$api_ready_code" != "200" ]]; then
  echo "[FAIL] Ready checks failed (api=$api_ready_code)"
  exit 1
fi

create_code="$(curl -s -o /tmp/agentfactory_update_create.json -w "%{http_code}" \
  -X POST "http://127.0.0.1:${API_PORT}/task" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"text":"update_safe_probe","profile":"zai"}')"

if [[ "$create_code" != "201" ]]; then
  echo "[FAIL] Create task probe failed (code=$create_code)"
  cat /tmp/agentfactory_update_create.json || true
  exit 1
fi

echo "[OK] Safe update completed successfully."
echo "[OK] Ready checks: api=$api_ready_code"
echo "[OK] Probe create: $create_code"
