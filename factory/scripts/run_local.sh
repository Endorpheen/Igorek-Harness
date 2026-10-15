#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$FACTORY_DIR"

ENV_FILE="${1:-.env.local}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE"
  echo "Copy .env.local.example to .env.local and set FACTORY_API_KEY"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${FACTORY_API_KEY:?FACTORY_API_KEY must be set in $ENV_FILE}"

API_PORT="${FACTORY_API_PORT:-8180}"

exec python3 factory_api.py --port "$API_PORT"
