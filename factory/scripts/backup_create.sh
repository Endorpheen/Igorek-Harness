#!/usr/bin/env bash
set -euo pipefail
umask 077

DEPLOY_ROOT="${1:-/opt/agentfactory}"
ENV_FILE="${2:-$DEPLOY_ROOT/.env.lan}"
FACTORY_DIR="$DEPLOY_ROOT/factory"

if [[ ! -d "$FACTORY_DIR" ]]; then
  echo "[FAIL] Factory directory not found: $FACTORY_DIR"
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

DB_PATH="${FACTORY_DB_PATH:-$FACTORY_DIR/database/factory.db}"
WORKSPACE_DIR="${FACTORY_WORKSPACE_DIR:-$FACTORY_DIR/workspace}"
BACKUP_DIR="${FACTORY_BACKUP_DIR:-$DEPLOY_ROOT/backups}"
RETENTION_DAYS="${FACTORY_BACKUP_RETENTION_DAYS:-14}"
TS="$(date -u +%Y%m%d-%H%M%SZ)"
BACKUP_BASENAME="agentfactory-backup-${TS}"
TMP_DIR="$(mktemp -d)"
PENDING_ARCHIVE=""

cleanup() {
  rm -rf "$TMP_DIR"
  if [[ -n "$PENDING_ARCHIVE" ]]; then rm -f -- "$PENDING_ARCHIVE"; fi
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"
if [[ -L "$BACKUP_DIR" ]]; then
  echo "[FAIL] Backup directory must not be a symlink"
  exit 1
fi
chmod 700 "$BACKUP_DIR"
PENDING_ARCHIVE="$(mktemp "$BACKUP_DIR/.${BACKUP_BASENAME}.XXXXXX.pending")"
BACKUP_FILENAME="${PENDING_ARCHIVE##*/}"
BACKUP_FILENAME="${BACKUP_FILENAME#.}"
BACKUP_ARCHIVE="$BACKUP_DIR/${BACKUP_FILENAME%.pending}.tar.gz"
mkdir -p "$TMP_DIR/factory/database"
mkdir -p "$TMP_DIR/factory/workspace"

if [[ ! -f "$DB_PATH" ]]; then
  echo "[FAIL] Database file not found: $DB_PATH"
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[FAIL] sqlite3 command is required for consistent DB backup"
  exit 1
fi

# Use sqlite online backup API for a consistent DB snapshot.
sqlite3 "$DB_PATH" ".timeout 5000" ".backup '$TMP_DIR/factory/database/factory.db'"

if [[ -d "$WORKSPACE_DIR" ]]; then
  cp -a "$WORKSPACE_DIR/." "$TMP_DIR/factory/workspace/"
fi

if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$TMP_DIR/.env.lan"
fi

cat > "$TMP_DIR/backup.meta" <<EOF
created_at_utc=$TS
deploy_root=$DEPLOY_ROOT
db_path=$DB_PATH
workspace_dir=$WORKSPACE_DIR
EOF

tar -czf "$PENDING_ARCHIVE" -C "$TMP_DIR" .
mv -T -- "$PENDING_ARCHIVE" "$BACKUP_ARCHIVE"
PENDING_ARCHIVE=""

find "$BACKUP_DIR" -type f -name 'agentfactory-backup-*.tar.gz' -mtime +"$RETENTION_DAYS" -delete || true

echo "[OK] Backup created: $BACKUP_ARCHIVE"
