#!/usr/bin/env bash
set -euo pipefail

API_PORT="${API_PORT:-8180}"
MAIN_PC_IP="${MAIN_PC_IP:-192.168.0.155}"

if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo API_PORT="$API_PORT" MAIN_PC_IP="$MAIN_PC_IP" bash "$0"
  fi
  echo "Run as root (or install sudo)."
  exit 1
fi

if command -v ufw >/dev/null 2>&1; then
  if ! ufw status | grep -q "Status: active"; then
    ufw --force enable
  fi

  allow_api_rule="${API_PORT}/tcp                   ALLOW IN    ${MAIN_PC_IP}"
  deny_api_rule="${API_PORT}/tcp                   DENY IN     Anywhere"

  ufw status | grep -F "$allow_api_rule" >/dev/null 2>&1 || ufw allow proto tcp from "$MAIN_PC_IP" to any port "$API_PORT"
  ufw status | grep -F "$deny_api_rule" >/dev/null 2>&1 || ufw deny "$API_PORT"/tcp

  echo "UFW rules applied: allow $MAIN_PC_IP for port $API_PORT, deny others."
  ufw status verbose
  exit 0
fi

if command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT -p tcp -s "$MAIN_PC_IP" --dport "$API_PORT" -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -p tcp -s "$MAIN_PC_IP" --dport "$API_PORT" -j ACCEPT

  iptables -C INPUT -p tcp --dport "$API_PORT" -j DROP 2>/dev/null || \
    iptables -A INPUT -p tcp --dport "$API_PORT" -j DROP

  echo "iptables rules applied. Persist rules with your distro tooling (e.g. iptables-persistent)."
  iptables -S INPUT | grep -E "dport ${API_PORT}"
  exit 0
fi

echo "Neither ufw nor iptables found."
exit 1
