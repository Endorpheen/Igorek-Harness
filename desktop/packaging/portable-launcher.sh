#!/usr/bin/env bash
set -euo pipefail
PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PACKAGE_PYTHON="$PACKAGE_ROOT/app/runtime/@PYTHON@"
"$PACKAGE_PYTHON" -I "$PACKAGE_ROOT/verify-package.py" "$PACKAGE_ROOT"
if [[ ${1:-} == --verify ]]; then
  exit 0
fi
POLICY_READY=false
INSTALL_ROOT="$(cd -- "$PACKAGE_ROOT/../.." && pwd -P)"
EXPECTED_INSTALLED="\"$INSTALL_ROOT/releases/*/electron/electron\""
EXPECTED_PORTABLE="\"$PACKAGE_ROOT/electron/electron\""
if [[ -f "$INSTALL_ROOT/installation.json" && -f /etc/apparmor.d/igorek-desktop-installed &&
      "$(cat /etc/apparmor.d/igorek-desktop-installed)" == *"$EXPECTED_INSTALLED"* ]]; then
  POLICY_READY=true
elif [[ -f /etc/apparmor.d/igorek-desktop-portable &&
        "$(cat /etc/apparmor.d/igorek-desktop-portable)" == *"$EXPECTED_PORTABLE"* ]]; then
  POLICY_READY=true
fi
if [[ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]] &&
   [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns)" == 1 ]] && [[ "$POLICY_READY" != true ]]; then
  echo 'Для песочницы Electron на Ubuntu требуется однократная настройка. Выполните:' >&2
  if [[ -f "$INSTALL_ROOT/installation.json" ]]; then
    printf 'sudo %q --installation-root %q\n' "$PACKAGE_ROOT/install-sandbox-policy.sh" "$INSTALL_ROOT" >&2
  else
    printf 'sudo %q --electron-binary %q --policy-name igorek-desktop-portable\n' "$PACKAGE_ROOT/install-sandbox-policy.sh" "$PACKAGE_ROOT/electron/electron" >&2
  fi
  exit 1
fi
exec "$PACKAGE_ROOT/electron/electron" "$PACKAGE_ROOT/app" --managed "$@"
