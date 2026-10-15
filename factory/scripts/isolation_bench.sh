#!/usr/bin/env bash
# Performance measurement harness for the per-session KVM isolation path (plan todo 27).
#
# Measures cold start, warm start, RAM overhead, CPU overhead, disk overhead,
# command latency, local-inference impact and workspace attach time, twice per
# invocation (variance), and writes:
#   * a raw JSON artifact  -> .omo/evidence/isolation-bench-<date>.json
#   * a human summary      -> .omo/evidence/isolation-bench-<date>.txt
# Every metric in the JSON carries the exact command that produced it and the
# environment it ran in (see tests/test_isolation_bench.py for the metric
# definitions and the methodology notes).
#
# Honesty rules:
#   * no /dev/kvm on this host -> the script prints `SKIP: no KVM (...)` and
#     exits 0 without writing any artifact and without printing a single number;
#   * the Python driver re-probes the structural readiness (/dev/kvm, libvirt,
#     virtiofsd, qemu-img, AF_VSOCK) and needs the explicit `KVM_AVAILABLE=1`
#     opt-in before it boots anything; it never prints PASS on its own;
#   * a failed/timed-out measurement exits 1 and no artifact is written (the
#     driver refuses partial or fabricated output);
#   * after a successful run the script asserts the JSON artifact exists and is
#     non-empty - a "success" without the artifact is a FAIL.
#
# Usage:
#   isolation_bench.sh [--profile NAME] [--workspace DIR] [--runs N]
#                      [--out-json PATH] [--out-summary PATH] [--probe-only]
#
# Options:
#   --profile NAME     existing cc-backend profile (default: $IGOREK_ISOLATION_BENCH_PROFILE or cc-wally)
#   --workspace DIR    existing absolute project workspace (default: $IGOREK_ISOLATION_BENCH_WORKSPACE or $PWD)
#   --runs N           consecutive measurement passes (default: 2)
#   --out-json PATH    raw JSON artifact path (default: .omo/evidence/isolation-bench-<date>.json)
#   --out-summary PATH human summary path (default: .omo/evidence/isolation-bench-<date>.txt)
#   --timeout-s N      whole-run budget (default: $IGOREK_ISOLATION_BENCH_TIMEOUT_S or 3600)
#   --probe-only       print the availability verdict and exit
#   -h, --help         this help
#
# Environment: KVM_AVAILABLE=1 is required to run the real benchmark on a KVM host.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DRIVER="$REPO_ROOT/tests/test_isolation_bench.py"

PROFILE="${IGOREK_ISOLATION_BENCH_PROFILE:-cc-wally}"
WORKSPACE="${IGOREK_ISOLATION_BENCH_WORKSPACE:-$PWD}"
RUNS="2"
OUT_DIR="$REPO_ROOT/.omo/evidence"
DATE="$(date +%F)"
OUT_JSON="$OUT_DIR/isolation-bench-$DATE.json"
OUT_SUMMARY="$OUT_DIR/isolation-bench-$DATE.txt"
RUN_TIMEOUT_S="${IGOREK_ISOLATION_BENCH_TIMEOUT_S:-3600}"
PROBE_ONLY=0

usage() {
  cat <<'USAGE'
Usage: isolation_bench.sh [--profile NAME] [--workspace DIR] [--runs N]
                          [--out-json PATH] [--out-summary PATH] [--probe-only]

Options:
  --profile NAME      existing cc-backend profile (default: $IGOREK_ISOLATION_BENCH_PROFILE or cc-wally)
  --workspace DIR     existing absolute project workspace (default: $IGOREK_ISOLATION_BENCH_WORKSPACE or $PWD)
  --runs N            consecutive measurement passes (default: 2)
  --out-json PATH     raw JSON artifact path (default: .omo/evidence/isolation-bench-<date>.json)
  --out-summary PATH  human summary path (default: .omo/evidence/isolation-bench-<date>.txt)
  --timeout-s N       whole-run budget (default: 3600)
  --probe-only        print the availability verdict and exit
  -h, --help          this help

Environment: KVM_AVAILABLE=1 is required to run; without /dev/kvm the script
prints `SKIP: no KVM` and exits 0.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --profile" >&2; exit 1; }; PROFILE="$2"; shift 2 ;;
    --workspace) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --workspace" >&2; exit 1; }; WORKSPACE="$2"; shift 2 ;;
    --runs) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --runs" >&2; exit 1; }; RUNS="$2"; shift 2 ;;
    --out-json) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --out-json" >&2; exit 1; }; OUT_JSON="$2"; shift 2 ;;
    --out-summary) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --out-summary" >&2; exit 1; }; OUT_SUMMARY="$2"; shift 2 ;;
    --timeout-s) [[ $# -ge 2 ]] || { echo "FAIL: missing value for --timeout-s" >&2; exit 1; }; RUN_TIMEOUT_S="$2"; shift 2 ;;
    --probe-only) PROBE_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# The structural gate: without the KVM device nothing can be measured, and the
# script says so instead of printing numbers. The driver re-probes anyway.
if [[ ! -e /dev/kvm ]]; then
  echo "SKIP: no KVM (no /dev/kvm on this host)"
  exit 0
fi

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

DRIVER_ARGS=(
  --real
  --profile "$PROFILE"
  --workspace "$WORKSPACE"
  --runs "$RUNS"
  --out "$OUT_JSON"
  --summary "$OUT_SUMMARY"
)
[[ "$PROBE_ONLY" == "1" ]] && DRIVER_ARGS=(--probe-only)

mkdir -p "$(dirname "$OUT_JSON")"

echo "RUN: profile=$PROFILE workspace=$WORKSPACE runs=$RUNS out_json=$OUT_JSON"
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
  0)
    # A success without the artifact is not a success.
    if [[ "$PROBE_ONLY" != "1" && ( ! -s "$OUT_JSON" ) ]]; then
      echo "FAIL: the run reported success but wrote no JSON artifact at $OUT_JSON" >&2
      exit 1
    fi
    exit 0
    ;;
  2) exit 0 ;;  # the driver printed SKIP: no KVM and measured nothing
  124|137)
    echo "FAIL: bench driver exceeded ${RUN_TIMEOUT_S}s and was killed"
    exit 1
    ;;
  *) exit 1 ;;
esac
