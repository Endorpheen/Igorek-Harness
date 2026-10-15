#!/usr/bin/env bash
set -euo pipefail
umask 077
ARCHIVE_TOOL="$(dirname "${BASH_SOURCE[0]}")/backup_archive.py"

DEPLOY_ROOT="${1:-/opt/agentfactory}"
ARCHIVE_PATH="${2:-}"
ENV_FILE="${3:-$DEPLOY_ROOT/.env.lan}"
YES_FLAG="${4:-}"
FACTORY_DIR="$DEPLOY_ROOT/factory"

if [[ -z "$ARCHIVE_PATH" ]]; then
  echo "Usage: $0 <deploy_root> <archive_path> [env_file] [--yes]"
  exit 1
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "[FAIL] Backup archive not found: $ARCHIVE_PATH"
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

DB_PATH="${FACTORY_DB_PATH:-$FACTORY_DIR/database/factory.db}"
WORKSPACE_DIR="${FACTORY_WORKSPACE_DIR:-$FACTORY_DIR/workspace}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ "$YES_FLAG" != "--yes" ]]; then
  echo "This will overwrite DB and workspace from backup:"
  echo "  DB: $DB_PATH"
  echo "  Workspace: $WORKSPACE_DIR"
  echo "Type 'YES' to continue:"
  read -r answer
  if [[ "$answer" != "YES" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

python3 "$ARCHIVE_TOOL" extract "$ARCHIVE_PATH" "$TMP_DIR"

if [[ ! -f "$TMP_DIR/factory/database/factory.db" ]]; then
  echo "[FAIL] Backup archive does not contain factory DB snapshot"
  exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"
mkdir -p "$WORKSPACE_DIR"

if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl stop agentfactory-api.service 2>/dev/null || true
fi

python3 "$ARCHIVE_TOOL" database "$TMP_DIR/factory/database/factory.db" "$DB_PATH"

if [[ -d "$TMP_DIR/factory/workspace" ]]; then
  rm -rf "$WORKSPACE_DIR"
  mkdir -p "$WORKSPACE_DIR"
  cp -a --no-dereference "$TMP_DIR/factory/workspace/." "$WORKSPACE_DIR/"
fi

if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl start agentfactory-api.service 2>/dev/null || true
fi

echo "[OK] Backup restored from: $ARCHIVE_PATH"
