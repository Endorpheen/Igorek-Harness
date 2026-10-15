#!/usr/bin/env bash
set -euo pipefail
DESKTOP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(basename -- "$DESKTOP_ROOT")" == packaging ]]; then
  DESKTOP_ROOT="$(cd -- "$DESKTOP_ROOT/.." && pwd)"
fi
ELECTRON_BINARY="$DESKTOP_ROOT/node_modules/electron/dist/electron"
POLICY_NAME='igorek-desktop-prototype'
INSTALL_ROOT=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --electron-binary) ELECTRON_BINARY="${2:?Укажите путь к Electron}"; shift 2 ;;
    --installation-root) INSTALL_ROOT="${2:?Укажите каталог установки}"; POLICY_NAME=igorek-desktop-installed; shift 2 ;;
    --policy-name) POLICY_NAME="${2:?Укажите имя профиля}"; shift 2 ;;
    *) echo 'Неизвестный параметр.' >&2; exit 1 ;;
  esac
done
if [[ "$POLICY_NAME" != igorek-desktop-prototype && "$POLICY_NAME" != igorek-desktop-portable && "$POLICY_NAME" != igorek-desktop-installed ]]; then
  echo 'Неизвестное имя профиля песочницы.' >&2; exit 1
fi
if [[ ${EUID} -ne 0 ]]; then
  echo 'Запустите этот скрипт через sudo: он устанавливает профиль AppArmor для песочницы Electron.' >&2
  exit 1
fi
SAFE_PATH_PATTERN='^[[:alnum:]_./ -]+$'
if [[ -n "$INSTALL_ROOT" ]]; then
  if [[ "$POLICY_NAME" != igorek-desktop-installed || "$INSTALL_ROOT" != /* || -L "$INSTALL_ROOT" || ! "$INSTALL_ROOT" =~ $SAFE_PATH_PATTERN || ! -f "$INSTALL_ROOT/installation.json" || ! -x "$INSTALL_ROOT/active/current/electron/electron" ]]; then
    echo 'Некорректный каталог установки.' >&2; exit 1
  fi
  INSTALL_ROOT="$(cd -- "$INSTALL_ROOT" && pwd -P)"
  ELECTRON_BINARY="$INSTALL_ROOT/releases/*/electron/electron"
else
  if [[ "$POLICY_NAME" == igorek-desktop-installed || "$ELECTRON_BINARY" != /* || ! -x "$ELECTRON_BINARY" || -L "$ELECTRON_BINARY" || ! "$ELECTRON_BINARY" =~ $SAFE_PATH_PATTERN ]]; then
    echo 'Некорректный путь к Electron.' >&2; exit 1
  fi
fi
POLICY_TEMP="$(mktemp)"
trap 'rm -f -- "$POLICY_TEMP"' EXIT
cat > "$POLICY_TEMP" <<POLICY
abi <abi/5.0>,
include <tunables/global>

profile $POLICY_NAME "$ELECTRON_BINARY" flags=(unconfined) {
  userns,
  @{exec_path} mr,
}
POLICY
/usr/sbin/apparmor_parser -Q -T "$POLICY_TEMP"
install -o root -g root -m 0644 "$POLICY_TEMP" "/etc/apparmor.d/$POLICY_NAME"
/usr/sbin/apparmor_parser -r "/etc/apparmor.d/$POLICY_NAME"
printf '%s\n' 'Профиль песочницы Игорька установлен. Приложение запускайте обычным пользователем.'
