#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_VERSION="${IGOREK_HOST_TOOL_PYTHON:-$(cat "$REPO_ROOT/.python-version")}"

BUILD_STAGE="$(mktemp -d -t igorek-host-tool-XXXXXXXX)"
trap 'rm -rf -- "$BUILD_STAGE"' EXIT

uv export --project "$REPO_ROOT" --locked --no-dev --no-emit-project \
  --format requirements.txt --output-file "$BUILD_STAGE/requirements.txt"
uv run --no-project --python "$PYTHON_VERSION" python \
  "$REPO_ROOT/tools/build_wheel.py" --output "$BUILD_STAGE/wheels"

WHEEL_FILES=("$BUILD_STAGE"/wheels/*.whl)
if [[ ${#WHEEL_FILES[@]} -ne 1 || ! -f "${WHEEL_FILES[0]}" ]]; then
  echo 'Expected exactly one verified project wheel.' >&2
  exit 1
fi
uv tool install --python "$PYTHON_VERSION" --force --reinstall --link-mode copy \
  --constraints "$BUILD_STAGE/requirements.txt" "${WHEEL_FILES[0]}"
