#!/usr/bin/env bash
# End-to-end PoC harness for the per-session KVM isolation boundary (plan todo 13).
#
# Runs one real session through the isolation stack and prints one line per
# assertion (PASS/FAIL), plus a CLEANUP receipt and a final RESULT. The driver
# lives in tests/test_isolation_poc.py; this script is only the runnable entry:
# it resolves the interpreter, enforces the availability opt-in and bounds the
# whole run with `timeout`.
#
# Honesty rules:
#   * KVM_AVAILABLE must be an explicit 1/true/yes/on; otherwise the script
#     prints `SKIP: no KVM (...)` and exits 0 without touching anything;
#   * the driver re-checks the structural probe (/dev/kvm, libvirt, virtiofsd,
#     qemu-img, AF_VSOCK) and prints its own `SKIP: no KVM (<reason>)` when the
#     host is not ready - this script never prints PASS on its own;
#   * a FAIL from any assertion (or a leftover from cleanup) exits 1;
#   * a driver killed by the timeout exits 1 and the EXIT trap removes the
#     scratch root as a last-resort cleanup backstop.
#
# Usage:
#   isolation_poc.sh --profile <existing profile> --workspace <abs dir> [options]
#   isolation_poc.sh --probe-only
#
# Options:
#   --profile NAME        existing profile (default: $IGOREK_ISOLATION_POC_PROFILE or cc-wally)
#   --workspace DIR       existing absolute project workspace (default: $PWD)
#   --session-id ID       session id override
#   --vsock-port N        host vsock port (default: 43011)
#   --boot-timeout-s N    guest boot budget (default: 240)
#   --wait-timeout-s N    per-step budget (default: 120)
#   --timeout-s N         whole-run budget (default: 1200)
#   --poc-root DIR        scratch root override (default: a temp dir under the data root)
#   --probe-only          print the availability verdict and exit
#   -h, --help            this help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DRIVER="$REPO_ROOT/tests/test_isolation_poc.py"

PROFILE="${IGOREK_ISOLATION_POC_PROFILE:-cc-wally}"
WORKSPACE="${IGOREK_ISOLATION_POC_WORKSPACE:-$PWD}"
SESSION_ID=""
VSOCK_PORT="${IGOREK_ISOLATION_VSOCK_PORT:-43011}"
BOOT_TIMEOUT_S="240"
WAIT_TIMEOUT_S="120"
RUN_TIMEOUT_S="${IGOREK_ISOLATION_POC_TIMEOUT_S:-1200}"
POC_ROOT=""
PROBE_ONLY=0

usage() {
  cat <<'USAGE'
Usage: isolation_poc.sh --profile <existing profile> --workspace <abs dir> [options]
       isolation_poc.sh --probe-only

Options:
  --profile NAME        existing profile (default: $IGOREK_ISOLATION_POC_PROFILE or cc-wally)
  --workspace DIR       existing absolute project workspace (default: $PWD)
  --session-id ID       session id override
  --vsock-port N        host vsock port (default: 43011)
  --boot-timeout-s N    guest boot budget (default: 240)
  --wait-timeout-s N    per-step budget (default: 120)
  --timeout-s N         whole-run budget (default: 1200)
  --poc-root DIR        scratch root override (default: a temp dir)
  --probe-only          print the availability verdict and exit
  -h, --help            this help

Environment: KVM_AVAILABLE=1 is required to run; without it the script prints
`SKIP: no KVM` and exits 0.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --profile" >&2; exit 1; }; PROFILE="$2"; shift 2 ;;
    --workspace) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --workspace" >&2; exit 1; }; WORKSPACE="$2"; shift 2 ;;
    --session-id) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --session-id" >&2; exit 1; }; SESSION_ID="$2"; shift 2 ;;
    --vsock-port) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --vsock-port" >&2; exit 1; }; VSOCK_PORT="$2"; shift 2 ;;
    --boot-timeout-s) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --boot-timeout-s" >&2; exit 1; }; BOOT_TIMEOUT_S="$2"; shift 2 ;;
    --wait-timeout-s) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --wait-timeout-s" >&2; exit 1; }; WAIT_TIMEOUT_S="$2"; shift 2 ;;
    --timeout-s) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --timeout-s" >&2; exit 1; }; RUN_TIMEOUT_S="$2"; shift 2 ;;
    --poc-root) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --poc-root" >&2; exit 1; }; POC_ROOT="$2"; shift 2 ;;
    --probe-only) PROBE_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# The explicit opt-in: without it nothing is probed, booted or touched.
case "$(printf '%s' "${KVM_AVAILABLE:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on|kvm) ;;
  *) echo "SKIP: no KVM (KVM_AVAILABLE unset; set KVM_AVAILABLE=1 on a KVM host)"; exit 0 ;;
esac

if [[ ! -f "$DRIVER" ]]; then
  echo "FAIL: driver not found: $DRIVER" >&2
  exit 1
fi

PYTHON=()
if [[ -n "${IGOREK_ISOLATION_PYTHON:-}" ]]; then
  PYTHON=("$IGOREK_ISOLATION_PYTHON")
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON=("$REPO_ROOT/.venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
  PYTHON=(uv run --no-sync python)
else
  PYTHON=(python3)
fi

if [[ -z "$POC_ROOT" ]]; then
  POC_ROOT="$(mktemp -d -t igorek-isolation-poc.XXXXXX)"
else
  if [[ "$POC_ROOT" != /* || -e "$POC_ROOT" || -L "$POC_ROOT" ]]; then
    echo "FAIL: --poc-root must be a new absolute directory" >&2
    exit 1
  fi
  if ! mkdir -- "$POC_ROOT"; then
    echo "FAIL: cannot create --poc-root: $POC_ROOT" >&2
    exit 1
  fi
fi

cleanup_root() {
  local rc=$?
  if [[ -d "$POC_ROOT" ]]; then
    rm -rf -- "$POC_ROOT"
    if [[ -d "$POC_ROOT" ]]; then
      echo "CLEANUP: LEFTOVER poc root $POC_ROOT"
      rc=1
    else
      echo "CLEANUP: removed poc root $POC_ROOT (shell backstop)"
    fi
  fi
  exit "$rc"
}
trap cleanup_root EXIT

DRIVER_ARGS=(
  --real
  --profile "$PROFILE"
  --workspace "$WORKSPACE"
  --vsock-port "$VSOCK_PORT"
  --boot-timeout-s "$BOOT_TIMEOUT_S"
  --wait-timeout-s "$WAIT_TIMEOUT_S"
  --poc-root "$POC_ROOT"
)
[[ -n "$SESSION_ID" ]] && DRIVER_ARGS+=(--session-id "$SESSION_ID")
[[ "$PROBE_ONLY" == "1" ]] && DRIVER_ARGS=(--probe-only)

echo "RUN: profile=$PROFILE workspace=$WORKSPACE vsock_port=$VSOCK_PORT poc_root=$POC_ROOT"
if command -v timeout >/dev/null 2>&1; then
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    timeout --signal=TERM --kill-after=30 "$RUN_TIMEOUT_S" \
    "${PYTHON[@]}" -u "$DRIVER" "${DRIVER_ARGS[@]}"
  rc=$?
else
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "${PYTHON[@]}" -u "$DRIVER" "${DRIVER_ARGS[@]}"
  rc=$?
fi

case "$rc" in
  0) exit 0 ;;
  2) exit 0 ;;  # the driver printed SKIP: no KVM and did not run
  124|137)
    echo "FAIL: poc driver exceeded ${RUN_TIMEOUT_S}s and was killed"
    exit 1
    ;;
  *) exit 1 ;;
esac
