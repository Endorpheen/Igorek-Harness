#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${1:-/opt/agentfactory}"
ENV_FILE="${2:-$DEPLOY_ROOT/.env.lan}"
UNIT_SRC_DIR="$DEPLOY_ROOT/factory/deploy"
SYSTEMD_DIR="/etc/systemd/system"

if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo bash "$0" "$@"
  fi
  echo "Run as root (or install sudo)."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE"
  exit 1
fi

if [[ ! -d "$UNIT_SRC_DIR" ]]; then
  echo "Unit source directory not found: $UNIT_SRC_DIR"
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
if [[ -z "${FACTORY_API_KEY:-}" || "${FACTORY_API_KEY}" == "change_me" ]]; then
  echo "FACTORY_API_KEY is missing or set to change_me in $ENV_FILE"
  exit 1
fi

install -m 0644 "$UNIT_SRC_DIR/agentfactory-api.service" "$SYSTEMD_DIR/agentfactory-api.service"
if [[ -f "$SYSTEMD_DIR/agentfactory-monitor.service" ]]; then
  systemctl disable --now agentfactory-monitor.service 2>/dev/null || true
  rm -f "$SYSTEMD_DIR/agentfactory-monitor.service"
fi
install -m 0644 "$UNIT_SRC_DIR/agentfactory.target" "$SYSTEMD_DIR/agentfactory.target"

systemctl daemon-reload
systemctl enable --now agentfactory.target

echo "AgentFactory services installed and started."
systemctl --no-pager --full status agentfactory-api.service || true
