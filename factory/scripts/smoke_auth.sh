#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$FACTORY_DIR"

PROFILE="${1:-local}"
if [[ "$PROFILE" == "local" ]]; then
  ENV_FILE="${2:-.env.local}"
elif [[ "$PROFILE" == "lan" ]]; then
  ENV_FILE="${2:-.env.lan}"
else
  echo "Usage: $0 [local|lan] [env_file]"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${FACTORY_API_KEY:?FACTORY_API_KEY must be set in $ENV_FILE}"

if [[ "$PROFILE" == "local" ]]; then
  API_PORT="${FACTORY_API_PORT:-18180}"
else
  API_PORT="${FACTORY_API_PORT:-28180}"
fi
API_URL="http://127.0.0.1:${API_PORT}"

python3 factory_api.py --port "$API_PORT" >/tmp/factory_api_smoke_${PROFILE}.log 2>&1 &
API_PID=$!

cleanup() {
  for pid in "$API_PID"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

for _ in {1..50}; do
  api_ok=0
  if curl -fsS "$API_URL/health" >/dev/null 2>&1; then api_ok=1; fi
  if [[ $api_ok -eq 1 ]]; then
    break
  fi
  sleep 0.2
done

status_code=$(curl -s -o /tmp/smoke_no_key_create_${PROFILE}.json -w "%{http_code}" \
  -X POST "$API_URL/task" \
  -H "Content-Type: application/json" \
  -d '{"text":"smoke without key"}')
if [[ "$status_code" != "401" && "$status_code" != "503" ]]; then
  echo "[FAIL] POST /task without key expected 401/503, got $status_code"
  cat /tmp/smoke_no_key_create_${PROFILE}.json
  exit 1
fi

echo "[OK] POST /task without key -> $status_code"

create_resp=$(curl -sS -X POST "$API_URL/task" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FACTORY_API_KEY" \
  -d '{"text":"smoke task with key","profile":"zai"}')

task_id=$(printf '%s' "$create_resp" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("task_id",""))')
if [[ -z "$task_id" ]]; then
  echo "[FAIL] POST /task with key did not return task_id"
  echo "$create_resp"
  exit 1
fi

echo "[OK] POST /task with key -> task_id=$task_id"

stop_code=$(curl -s -o /tmp/smoke_stop_${PROFILE}.json -w "%{http_code}" \
  -X PUT "$API_URL/task/$task_id/stop" \
  -H "X-API-Key: $FACTORY_API_KEY")
if [[ "$stop_code" != "200" && "$stop_code" != "404" ]]; then
  echo "[FAIL] PUT /task/{id}/stop with key expected 200/404, got $stop_code"
  cat /tmp/smoke_stop_${PROFILE}.json
  exit 1
fi

echo "[OK] PUT /task/{id}/stop with key -> $stop_code"

del_code=$(curl -s -o /tmp/smoke_delete_${PROFILE}.json -w "%{http_code}" \
  -X DELETE "$API_URL/task/$task_id" \
  -H "X-API-Key: $FACTORY_API_KEY")
if [[ "$del_code" != "200" ]]; then
  echo "[FAIL] DELETE /task/{id} with key expected 200, got $del_code"
  cat /tmp/smoke_delete_${PROFILE}.json
  exit 1
fi

echo "[OK] DELETE /task/{id} with key -> $del_code"

echo "[PASS] smoke_auth ($PROFILE)"
