#!/usr/bin/env bash
set -euo pipefail
PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PACKAGE_PYTHON="$PACKAGE_ROOT/app/runtime/@PYTHON@"
if ! "$PACKAGE_PYTHON" -I "$PACKAGE_ROOT/verify-package.py" "$PACKAGE_ROOT"; then
  /usr/bin/python3 -I "$PACKAGE_ROOT/install-gui.py" --gtk "$@"
  exit 1
fi
exec /usr/bin/python3 -I "$PACKAGE_ROOT/install-gui.py" --gtk "$@"
