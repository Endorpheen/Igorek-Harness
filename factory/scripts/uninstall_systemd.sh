#!/usr/bin/env bash
set -euo pipefail

SYSTEMD_DIR="/etc/systemd/system"

if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo bash "$0" "$@"
  fi
  echo "Run as root (or install sudo)."
  exit 1
fi

systemctl disable --now agentfactory.target agentfactory-api.service agentfactory-monitor.service 2>/dev/null || true

rm -f \
  "$SYSTEMD_DIR/agentfactory.target" \
  "$SYSTEMD_DIR/agentfactory-api.service" \
  "$SYSTEMD_DIR/agentfactory-monitor.service"

systemctl daemon-reload
systemctl reset-failed

echo "AgentFactory systemd units removed."
