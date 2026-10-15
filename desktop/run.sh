#!/usr/bin/env bash
set -euo pipefail
DESKTOP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$DESKTOP_ROOT/node_modules/.bin/electron" ]]; then
  echo 'Зависимости прототипа не установлены. Выполните npm ci в каталоге desktop.' >&2
  exit 1
fi
if [[ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]] &&
   [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns)" == 1 ]] &&
   [[ ! -f /etc/apparmor.d/igorek-desktop-prototype ]]; then
  echo 'Ubuntu требует настройку песочницы Electron. Один раз выполните:' >&2
  printf 'sudo %q\n' "$DESKTOP_ROOT/packaging/install-sandbox-policy.sh" >&2
  exit 1
fi
exec "$DESKTOP_ROOT/node_modules/.bin/electron" "$DESKTOP_ROOT" "$@"
