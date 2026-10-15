#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$FACTORY_DIR"

PROFILE="${1:-local}"
pick_free_port() {
  python3 - "$1" <<'PY'
import socket
import sys

start = int(sys.argv[1])
for port in range(start, start + 400):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        continue
    s.close()
    print(port)
    break
else:
    sys.exit(1)
PY
}

if [[ "$PROFILE" == "local" ]]; then
  ENV_FILE="${2:-.env.local}"
  API_PORT="${FACTORY_API_PORT:-$(pick_free_port 38180)}"
elif [[ "$PROFILE" == "lan" ]]; then
  ENV_FILE="${2:-.env.lan}"
  API_PORT="${FACTORY_API_PORT:-$(pick_free_port 48180)}"
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

API_URL="http://127.0.0.1:${API_PORT}"
DB_PATH="${FACTORY_DB_PATH:-$FACTORY_DIR/database/factory.db}"
TEST_WATCHDOG_INTERVAL="${SMOKE_WATCHDOG_INTERVAL_S:-1}"
TEST_STUCK_TIMEOUT="${SMOKE_STUCK_TIMEOUT_S:-2}"

FACTORY_WATCHDOG_INTERVAL_S="$TEST_WATCHDOG_INTERVAL" \
FACTORY_STUCK_TIMEOUT_S="$TEST_STUCK_TIMEOUT" \
python3 factory_api.py --port "$API_PORT" >/tmp/factory_api_smoke_p1_${PROFILE}.log 2>&1 &
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

for _ in {1..60}; do
  api_ok=0
  if curl -fsS "$API_URL/health" >/dev/null 2>&1; then api_ok=1; fi
  if [[ $api_ok -eq 1 ]]; then
    break
  fi
  sleep 0.2
done

api_health=$(curl -s -o /tmp/smoke_p1_api_health_${PROFILE}.json -w "%{http_code}" "$API_URL/health")
api_ready=$(curl -s -o /tmp/smoke_p1_api_ready_${PROFILE}.json -w "%{http_code}" "$API_URL/ready")

if [[ "$api_health" != "200" || "$api_ready" != "200" ]]; then
  echo "[FAIL] health/ready status codes unexpected"
  echo "api_health=$api_health api_ready=$api_ready"
  exit 1
fi

echo "[OK] API /health -> $api_health"
echo "[OK] API /ready -> $api_ready"

TASK1_ID=$(python3 - <<'PY'
import uuid
print(str(uuid.uuid4()))
PY
)
OLD_TS=$(( $(date +%s) - 120 ))

sqlite3 "$DB_PATH" <<SQL
INSERT OR REPLACE INTO tasks (
  task_id, stage, status, profile, input_text,
  retry_count, max_retries, created_at, updated_at, started_at
) VALUES (
  '$TASK1_ID', 'main', 'processing', 'zai', 'watchdog smoke #1',
  0, 1, $OLD_TS, $OLD_TS, $OLD_TS
);
SQL

for _ in {1..20}; do
  row=$(sqlite3 "$DB_PATH" "SELECT COALESCE(status,''), COALESCE(retry_count,0) FROM tasks WHERE task_id='$TASK1_ID';")
  if [[ "$row" == "queued|1" ]]; then
    break
  fi
  sleep 1
done

row=$(sqlite3 "$DB_PATH" "SELECT COALESCE(status,''), COALESCE(retry_count,0) FROM tasks WHERE task_id='$TASK1_ID';")
if [[ "$row" != "queued|1" ]]; then
  echo "[FAIL] watchdog did not requeue task as expected, got: $row"
  exit 1
fi

echo "[OK] watchdog requeue policy -> $row"

sqlite3 "$DB_PATH" <<SQL
UPDATE tasks
SET status='processing', retry_count=1, max_retries=1,
    updated_at=$OLD_TS, started_at=$OLD_TS
WHERE task_id='$TASK1_ID';
SQL

for _ in {1..20}; do
  status=$(sqlite3 "$DB_PATH" "SELECT COALESCE(status,'') FROM tasks WHERE task_id='$TASK1_ID';")
  if [[ "$status" == "dead_letter" ]]; then
    break
  fi
  sleep 1
done

status=$(sqlite3 "$DB_PATH" "SELECT COALESCE(status,'') FROM tasks WHERE task_id='$TASK1_ID';")
if [[ "$status" != "dead_letter" ]]; then
  echo "[FAIL] watchdog did not move task to dead_letter, got: $status"
  exit 1
fi

echo "[OK] watchdog dead_letter policy -> $status"

event_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM events WHERE task_id='$TASK1_ID' AND event_type IN ('watchdog_requeued','watchdog_dead_letter');")
if [[ "$event_count" -lt 2 ]]; then
  echo "[FAIL] watchdog events were not recorded, count=$event_count"
  exit 1
fi

echo "[OK] watchdog events recorded -> $event_count"
echo "[PASS] smoke_p1 ($PROFILE)"
