#!/usr/bin/env bash
# Max-feasible-concurrent-VM probe for the admission budget (plan todo 33).
#
# Measures how many session VMs this host can boot concurrently and writes a
# JSON artifact plus a human summary. Honesty rules (mirroring the todo-27
# bench harness):
#   * without KVM/libvirt/virtiofsd/qemu-img or the guest base image the probe
#     records `SKIP: <reason>` and exits 0 with `max_vms: null` in the artifact;
#   * booting anything requires the explicit `KVM_AVAILABLE=1` opt-in; without
#     it the probe records `SKIP: kvm_available_optin_required`;
#   * a "success" without the artifact is a FAIL.
#
# Run it on the target KVM host for the real number:
#   KVM_AVAILABLE=1 ./factory/scripts/isolation_capacity_probe.sh
#
# Usage:
#   isolation_capacity_probe.sh [--max-vms N] [--workspace DIR]
#                               [--out-json PATH] [--out-summary PATH] [--probe-only]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MAX_VMS="${IGOREK_ISOLATION_CAPACITY_MAX:-0}"
WORKSPACE="${IGOREK_ISOLATION_CAPACITY_WORKSPACE:-$PWD}"
OUT_DIR="$REPO_ROOT/.omo/evidence"
DATE="$(date +%F)"
OUT_JSON="$OUT_DIR/isolation-capacity-probe-$DATE.json"
OUT_SUMMARY="$OUT_DIR/isolation-capacity-probe-$DATE.txt"
PROBE_ONLY=0

usage() {
  cat <<'USAGE'
Usage: isolation_capacity_probe.sh [--max-vms N] [--workspace DIR]
                                   [--out-json PATH] [--out-summary PATH] [--probe-only]

Options:
  --max-vms N        hard upper bound for the measurement (0 = physical ceiling)
  --workspace DIR    existing absolute project workspace (default: $PWD)
  --out-json PATH    raw JSON artifact path
  --out-summary PATH human summary path
  --probe-only       print the structural readiness verdict and exit
  -h, --help         this help

Environment: KVM_AVAILABLE=1 is required to boot anything; without it the probe
records an honest SKIP and exits 0.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-vms) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --max-vms" >&2; exit 1; }; MAX_VMS="$2"; shift 2 ;;
    --workspace) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --workspace" >&2; exit 1; }; WORKSPACE="$2"; shift 2 ;;
    --out-json) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --out-json" >&2; exit 1; }; OUT_JSON="$2"; shift 2 ;;
    --out-summary) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --out-summary" >&2; exit 1; }; OUT_SUMMARY="$2"; shift 2 ;;
    --probe-only) PROBE_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

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

MODULE_ARGS=(
  --max-vms "$MAX_VMS"
  --workspace "$WORKSPACE"
  --out-json "$OUT_JSON"
  --out-summary "$OUT_SUMMARY"
)
[[ "$PROBE_ONLY" == "1" ]] && MODULE_ARGS=(--probe-only)

mkdir -p "$(dirname "$OUT_JSON")"

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  "${PYTHON[@]}" -u -m factory.core.isolation.capacity_probe "${MODULE_ARGS[@]}"
rc=$?

case "$rc" in
  0)
    if [[ "$PROBE_ONLY" != "1" && ( ! -s "$OUT_JSON" ) ]]; then
      echo "FAIL: the probe reported success but wrote no JSON artifact at $OUT_JSON" >&2
      exit 1
    fi
    exit 0
    ;;
  2) exit 0 ;;  # honest SKIP (the module already printed the reason and the artifact)
  *) exit 1 ;;
esac
