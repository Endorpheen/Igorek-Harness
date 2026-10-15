#!/usr/bin/env bash
# Frozen guest base image recipe for per-session KVM isolation (base.qcow2).
#
# Builds a minimal Debian guest image containing: python3 + uv, node, git,
# bubblewrap, the pinned Igorek source snapshot with `uv tool install`, a non-root
# `agent` user, the unprivileged-userns sysctl policy, a
# `bwrap-userns-restrict`-class AppArmor profile and the
# `igorek-guest-bwrap-probe` readiness probe (which runs the hardened
# `--unshare-all`-class bwrap argv as the agent user). The default kernel is the
# small Debian cloud kernel (linux-image-cloud-amd64, no initrd bloat); set
# IGOREK_ISOLATION_KERNEL_PACKAGE to install a vendor microvm kernel instead
# (kernel size dominates cold start, per the research notes in
# .omo/plans/kvm-session-isolation.md).
#
# Verification is separated from building: `--verify-only` re-hashes the image
# against `<image>.manifest.json` (factory/core/isolation/base_image.py, CLI in
# base_image_cli.py) and exits 0 only for a fully valid image. A manifest
# records sha256, tool/repo version, build date and the supply-chain refresh
# policy; a stale manifest (previous tool version) is rejected.
#
# Network: ONLY the real build path needs network. It contacts the Debian
# mirror (rootfs/kernel packages), registry.npmjs.org / Debian node packages,
# github.com/astral-sh/uv (pinned uv tarball, sha256-checkable via
# IGOREK_ISOLATION_UV_SHA256), and the Python package index. The pinned
# source snapshot is archived from the host checkout without credentials. `--verify-only` and `--dry-run` never touch the network.
#
# Safety: no secrets, no host data dir. The script never reads or copies
# ~/.igorek-agent-factory (except that the default OUTPUT path lives under the
# data root, set by get_data_root()); the guest gets no credentials, no host
# sockets and no host paths. Every external command is wrapped in `timeout`
# (IGOREK_ISOLATION_BUILD_TIMEOUT_S, default 1800 s), so a hung builder cannot
# hang the caller. If no supported builder is installed the script prints
# `SKIP: ...` and exits 0 - never a fake PASS.
#
# Usage:
#   build_isolation_image.sh [--verify-only] [--image PATH] [--manifest PATH]
#                            [--expected-tool-version V] [--tool-version V]
#                            [--force] [--dry-run] [--help]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PYTHON_BIN="${IGOREK_ISOLATION_PYTHON:-python3}"
DEFAULT_DATA_ROOT="${IGOREK_FACTORY_HOME:-$HOME/.igorek-agent-factory}"
IMAGE_PATH="${IGOREK_ISOLATION_BASE_IMAGE:-$DEFAULT_DATA_ROOT/isolation/images/base.qcow2}"
MANIFEST_PATH=""
TOOL_VERSION="${IGOREK_ISOLATION_TOOL_VERSION:-}"
EXPECTED_TOOL_VERSION="${IGOREK_ISOLATION_EXPECTED_TOOL_VERSION:-}"
VERIFY_ONLY=0
DRY_RUN=0
FORCE=0

BUILD_TIMEOUT_S="${IGOREK_ISOLATION_BUILD_TIMEOUT_S:-1800}"
VERIFY_TIMEOUT_S="${IGOREK_ISOLATION_VERIFY_TIMEOUT_S:-120}"
DISTRO="${IGOREK_ISOLATION_DISTRO:-debian-13}"
SUITE="${IGOREK_ISOLATION_SUITE:-trixie}"
MIRROR="${IGOREK_ISOLATION_MIRROR:-http://deb.debian.org/debian}"
GUEST_ARCH_X86_64="${IGOREK_ISOLATION_ARCH_X86_64:-x86_64}"
GUEST_ARCH_DEB="${IGOREK_ISOLATION_ARCH_DEB:-amd64}"
DISK_SIZE="${IGOREK_ISOLATION_DISK_SIZE:-20G}"
RAW_SIZE="${IGOREK_ISOLATION_RAW_SIZE:-8G}"
KERNEL_PACKAGE="${IGOREK_ISOLATION_KERNEL_PACKAGE:-linux-image-cloud-amd64}"
GUEST_USER="${IGOREK_ISOLATION_GUEST_USER:-agent}"
UV_VERSION="${IGOREK_ISOLATION_UV_VERSION:-0.13.1}"
UV_SHA256="${IGOREK_ISOLATION_UV_SHA256:-eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220}"
REPO_COMMIT="${IGOREK_ISOLATION_REPO_COMMIT:-}"
INCLUDE_WORKTREE="${IGOREK_ISOLATION_INCLUDE_WORKTREE:-0}"
BUILDER="${IGOREK_ISOLATION_BUILDER:-auto}"
TEMPLATE_IMAGE="${IGOREK_ISOLATION_TEMPLATE_IMAGE:-}"
GUEST_REPO_DIR="/opt/igorek/app"

# bwrap-readiness contract (plan todo 14): cross-checked by
# tests/test_isolation_guest_bwrap.py against isolation/guest_bwrap.py.
GUEST_USERNS_SYSCTL_FILE="/etc/sysctl.d/90-igorek-userns.conf"
GUEST_USERNS_SYSCTL_CONTENT="kernel.unprivileged_userns_clone = 1
user.max_user_namespaces = 10000"
GUEST_APPARMOR_RESTRICT_SYSCTL="kernel.apparmor_restrict_unprivileged_userns = 0"
APPARMOR_PROFILE_NAME="bwrap-userns-restrict"
APPARMOR_PROFILE_PATH="/etc/apparmor.d/${APPARMOR_PROFILE_NAME}"
APPARMOR_USENS_RULE="userns,"
USERNS_POLICY_RECORD="kernel.unprivileged_userns_clone=1,user.max_user_namespaces=10000"
GUEST_BWRAP_PROBE_BIN="/usr/local/bin/igorek-guest-bwrap-probe"

# Egress-guard contract (plan todo 40): cross-checked by
# tests/test_isolation_egress_enforce.py against isolation/egress_enforce.py.
EGRESS_UNIT_NAME="igorek-egress-guard.service"
GUEST_EGRESS_UNIT_PATH="/etc/systemd/system/${EGRESS_UNIT_NAME}"
GUEST_EGRESS_BOOT_BIN="/usr/local/bin/igorek-egress-boot"

usage() {
  cat <<'USAGE'
Usage: build_isolation_image.sh [options]

Options:
  --verify-only                 Verify image + manifest; never build. Exit 0 only
                                when the image is fully valid.
  --image PATH                  Image path (default: $IGOREK_ISOLATION_BASE_IMAGE
                                or <data root>/isolation/images/base.qcow2).
  --manifest PATH               Manifest override for verification.
  --expected-tool-version V     Refuse the image when its recorded tool version
                                differs (boot handshake version assert).
  --tool-version V              Tool version recorded by a build (default:
                                resolved from the installed package/pyproject).
  --force                       Overwrite an existing image/manifest.
  --dry-run                     Print the build plan; touch nothing; print no
                                success marker.
  -h, --help                    Show this help.
USAGE
}

log() { printf '%s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

run_bounded() { # run_bounded <timeout-seconds> <command...>
  local limit="$1"
  shift
  if have timeout; then
    timeout --signal=TERM --kill-after=30 "$limit" "$@"
  else
    "$@"
  fi
}

manifest_tool() { # manifest_tool <verify|manifest> [args...]
  local pythonpath="$REPO_ROOT"
  if [[ -n "${PYTHONPATH:-}" ]]; then
    pythonpath="$REPO_ROOT:$PYTHONPATH"
  fi
  run_bounded "$VERIFY_TIMEOUT_S" env PYTHONPATH="$pythonpath" \
    "$PYTHON_BIN" -m factory.core.isolation.base_image_cli "$@"
}

resolved_tool_version() {
  local pythonpath="$REPO_ROOT"
  if [[ -n "${PYTHONPATH:-}" ]]; then
    pythonpath="$REPO_ROOT:$PYTHONPATH"
  fi
  run_bounded "$VERIFY_TIMEOUT_S" env PYTHONPATH="$pythonpath" "$PYTHON_BIN" -c \
    'from factory.core.isolation.base_image import resolve_tool_version; print(resolve_tool_version())'
}

run_verify() {
  local args=(verify --image "$IMAGE_PATH")
  [[ -n "$MANIFEST_PATH" ]] && args+=(--manifest "$MANIFEST_PATH")
  [[ -n "$EXPECTED_TOOL_VERSION" ]] && args+=(--expected-tool-version "$EXPECTED_TOOL_VERSION")
  manifest_tool "${args[@]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only) VERIFY_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --image)
      [[ $# -ge 2 ]] || fail "missing value for --image"
      IMAGE_PATH="$2"; shift 2 ;;
    --manifest)
      [[ $# -ge 2 ]] || fail "missing value for --manifest"
      MANIFEST_PATH="$2"; shift 2 ;;
    --expected-tool-version)
      [[ $# -ge 2 ]] || fail "missing value for --expected-tool-version"
      EXPECTED_TOOL_VERSION="$2"; shift 2 ;;
    --tool-version)
      [[ $# -ge 2 ]] || fail "missing value for --tool-version"
      TOOL_VERSION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

if [[ "$VERIFY_ONLY" == "1" ]]; then
  run_verify
  exit 0
fi

if [[ "$DRY_RUN" == "1" ]]; then
  log "PLAN: build frozen guest base image (dry run, nothing is executed)"
  log "PLAN: output image   : $IMAGE_PATH"
  log "PLAN: output manifest: ${IMAGE_PATH}.manifest.json"
  log "PLAN: distro/suite   : $DISTRO / $SUITE ($MIRROR)"
  log "PLAN: kernel package : $KERNEL_PACKAGE"
  log "PLAN: guest user     : $GUEST_USER"
  log "PLAN: guest userns   : $USERNS_POLICY_RECORD"
  log "PLAN: apparmor       : ${APPARMOR_PROFILE_PATH} (${APPARMOR_USENS_RULE})"
  log "PLAN: bwrap probe    : ${GUEST_BWRAP_PROBE_BIN} --probe"
  log "PLAN: egress guard   : ${GUEST_EGRESS_UNIT_PATH} -> ${GUEST_EGRESS_BOOT_BIN} --boot-baseline"
  log "PLAN: source archive : ${REPO_COMMIT:-<HEAD>} -> $GUEST_REPO_DIR"
  log "PLAN: builder        : virt-builder (fallback: debootstrap)"
  log "PLAN: bounded by     : IGOREK_ISOLATION_BUILD_TIMEOUT_S=$BUILD_TIMEOUT_S"
  log "PLAN: after the build the manifest is written and verified; the success"
  log "PLAN: marker is printed only by the verifier for a fully valid image"
  exit 0
fi

if ! have virt-builder || ! have virt-customize || ! have qemu-img; then
  if ! have debootstrap || ! have qemu-img; then
    log "SKIP: no supported image builder on this host"
    log "SKIP: need virt-builder+virt-customize+qemu-img (install libguestfs-tools) or debootstrap+qemu-img"
    exit 0
  fi
fi

if [[ -z "$REPO_COMMIT" ]]; then
  REPO_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ -z "$REPO_COMMIT" ]]; then
  fail "cannot resolve the Igorek source commit"
fi

reject_unsafe_value() { # reject_unsafe_value <name> <value>
  local name="$1" value="$2"
  case "$value" in
    *"'"*|*$'\n'*) fail "$name contains an unsupported quote/newline" ;;
  esac
}

for name in REPO_COMMIT GUEST_USER KERNEL_PACKAGE UV_VERSION UV_SHA256; do
  reject_unsafe_value "$name" "${!name}"
done

WORK_DIR="$(mktemp -d -t igorek-isolation-build.XXXXXX)"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT
REPO_ARCHIVE="$WORK_DIR/repo.tar"
git -C "$REPO_ROOT" cat-file -e "${REPO_COMMIT}^{commit}" ||
  fail "source commit is not available locally: $REPO_COMMIT"
git -C "$REPO_ROOT" archive --format=tar --output="$REPO_ARCHIVE" "$REPO_COMMIT" ||
  fail "cannot archive source commit: $REPO_COMMIT"
if [[ "$INCLUDE_WORKTREE" == "1" ]]; then
  SOURCE_TREE="$WORK_DIR/repo-source"
  install -d -m 0755 "$SOURCE_TREE"
  tar -xf "$REPO_ARCHIVE" -C "$SOURCE_TREE"
  git -C "$REPO_ROOT" diff --binary "$REPO_COMMIT" -- factory/ pyproject.toml uv.lock |
    git -C "$SOURCE_TREE" apply --binary - ||
    fail "cannot apply tracked runtime changes to the guest source archive"
  # New Python modules are not part of git diff until tracked. Include only
  # runtime source modules, never ignored data or arbitrary untracked files.
  while IFS= read -r -d '' extra_source; do
    case "$extra_source" in
      factory/*.py)
        install -D -m 0644 "$REPO_ROOT/$extra_source" "$SOURCE_TREE/$extra_source"
        ;;
    esac
  done < <(git -C "$REPO_ROOT" ls-files --others --exclude-standard -z -- factory/)
  tar -C "$SOURCE_TREE" -cf "$REPO_ARCHIVE" . ||
    fail "cannot rebuild the guest source archive"
fi

render_guest_env() { # render_guest_env <output-file>
  local target="$1"
  {
    printf "IGOREK_GUEST_USER='%s'\n" "$GUEST_USER"
    printf "IGOREK_GUEST_KERNEL='%s'\n" "$KERNEL_PACKAGE"
    printf "IGOREK_GUEST_UV_VERSION='%s'\n" "$UV_VERSION"
    printf "IGOREK_GUEST_UV_SHA256='%s'\n" "$UV_SHA256"
    printf "IGOREK_GUEST_ARCH='%s'\n" "$GUEST_ARCH_X86_64"
    printf "IGOREK_GUEST_REPO_DIR='%s'\n" "$GUEST_REPO_DIR"
  } > "$target"
}

render_guest_setup() { # render_guest_setup <output-file>
  local target="$1"
  cat > "$target" <<'GUEST_SETUP'
#!/bin/bash
# Runs inside the guest rootfs (virt-builder --run-command or chroot).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
source /opt/igorek/guest-env.sh

apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
  ca-certificates curl git python3 python3-venv python3-pip gcc libc6-dev linux-libc-dev \
  bubblewrap passwd sudo apparmor apparmor-utils procps iproute2 \
  nftables util-linux libcap2-bin \
  "${IGOREK_GUEST_KERNEL}"

# The browser CLI requires Node 24; Debian 13 supplies Node 20.
NODE_VERSION=24.21.0
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-x64.tar.xz"
NODE_BASE_URL="https://nodejs.org/download/release/v${NODE_VERSION}"
NODE_TMP="$(mktemp -d)"
trap 'rm -rf "$NODE_TMP"' EXIT
curl --retry 5 --retry-all-errors --retry-delay 2 --fail --location --silent --show-error "$NODE_BASE_URL/$NODE_ARCHIVE" -o "$NODE_TMP/$NODE_ARCHIVE"
curl --retry 5 --retry-all-errors --retry-delay 2 --fail --location --silent --show-error "$NODE_BASE_URL/SHASUMS256.txt" -o "$NODE_TMP/SHASUMS256.txt"
( cd "$NODE_TMP" && grep "  ${NODE_ARCHIVE}$" SHASUMS256.txt | sha256sum --check --status ) || {
  echo "Node.js archive checksum verification failed" >&2
  exit 1
}
tar -xJf "$NODE_TMP/$NODE_ARCHIVE" -C /usr/local --strip-components=1
rm -rf "$NODE_TMP"
trap - EXIT
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
test "$(node --version)" = "v${NODE_VERSION}"
npm install --global --prefix /usr/local --allow-scripts=agent-browser --no-audit --no-fund agent-browser@0.33.2
command -v agent-browser >/dev/null
agent-browser --version

# One small kernel only: drop the generic flavour if the base pulled it in.
apt-get purge -y linux-image-amd64 >/dev/null 2>&1 || true
apt-get clean
rm -rf /var/lib/apt/lists/*

install -d -m 0755 /etc/systemd/network
cat > /etc/systemd/network/20-igorek-usernet.network <<'NETWORK'
[Match]
Name=en*

[Network]
DHCP=ipv4
IPv6AcceptRA=no
NETWORK
systemctl disable networking.service >/dev/null 2>&1 || true
systemctl enable systemd-networkd.service >/dev/null

# Non-root agent user: locked password, no sudo, no host credentials.
if ! id -u "${IGOREK_GUEST_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --user-group "${IGOREK_GUEST_USER}"
fi
passwd --lock "${IGOREK_GUEST_USER}" >/dev/null
gpasswd -d "${IGOREK_GUEST_USER}" sudo >/dev/null 2>&1 || true
# Privilege is used only by the exact igorek-sudo Bash call after the host's
# normal mobile tool approval has resumed the guest task. The account remains
# password-locked; this rule has no credentials from the host.
printf '%s ALL=(root) NOPASSWD: ALL\n' "${IGOREK_GUEST_USER}" > /etc/sudoers.d/igorek-guest-agent
chmod 0440 /etc/sudoers.d/igorek-guest-agent
visudo -cf /etc/sudoers.d/igorek-guest-agent

# python3 + uv (pinned; optional sha256 pinning of the downloaded tarball).
curl -fsSL -o /tmp/uv.tar.gz \
  "https://github.com/astral-sh/uv/releases/download/${IGOREK_GUEST_UV_VERSION}/uv-${IGOREK_GUEST_ARCH}-unknown-linux-gnu.tar.gz"
if [[ -n "${IGOREK_GUEST_UV_SHA256}" ]]; then
  echo "${IGOREK_GUEST_UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c -
fi
tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1
rm -f /tmp/uv.tar.gz
chmod 0755 /usr/local/bin/uv /usr/local/bin/uvx

# Install the pinned source snapshot without passing git credentials to the guest.
# A reused template must not retain modules deleted from the current snapshot.
rm -rf -- "${IGOREK_GUEST_REPO_DIR}"
rm -rf -- /opt/igorek/runtime
rm -rf -- "/home/${IGOREK_GUEST_USER}/.local/share/uv/tools/igorek-agent-factory"
rm -f -- "/home/${IGOREK_GUEST_USER}/.local/bin/igorek-factory"
install -d -m 0755 "${IGOREK_GUEST_REPO_DIR}"
tar -xf /opt/igorek/repo.tar -C "${IGOREK_GUEST_REPO_DIR}"
chown -R root:root "${IGOREK_GUEST_REPO_DIR}"
rm -f /opt/igorek/repo.tar
# The privileged controller must never import agent-writable Python code.
export UV_PYTHON_INSTALL_DIR=/opt/igorek/python
uv python install "$(cat "${IGOREK_GUEST_REPO_DIR}/.python-version")"
uv venv --python "$(cat "${IGOREK_GUEST_REPO_DIR}/.python-version")" /opt/igorek/runtime
cd "${IGOREK_GUEST_REPO_DIR}"
uv export --frozen --no-dev --no-emit-project --format requirements.txt -o /tmp/igorek-runtime-requirements.txt
uv pip install --python /opt/igorek/runtime/bin/python --require-hashes -r /tmp/igorek-runtime-requirements.txt
uv pip install --python /opt/igorek/runtime/bin/python --no-deps .
rm -f /tmp/igorek-runtime-requirements.txt
# Standalone CPython omits AF_VSOCK. Reuse the pinned, verified desktop socket
# build and activate it from a root-owned site file before networking imports.
rm -rf -- /opt/igorek/native
printf '%s\n' '{"python":"runtime/bin/python"}' > /opt/igorek/manifest.json
/opt/igorek/runtime/bin/python -I "${IGOREK_GUEST_REPO_DIR}/desktop/packaging/bundle-socket.py" --runtime /opt/igorek
SOCKET_SITE="$(/opt/igorek/runtime/bin/python -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
install -m 0644 "${IGOREK_GUEST_REPO_DIR}/desktop/socket_bootstrap.py" "${SOCKET_SITE}/igorek_guest_socket.py"
printf '%s\n' 'import igorek_guest_socket; igorek_guest_socket.activate("/opt/igorek")' > "${SOCKET_SITE}/00_igorek_guest_socket.pth"
/opt/igorek/runtime/bin/python -I -c 'import socket; assert hasattr(socket,"AF_VSOCK")'
cat > /usr/local/bin/igorek-factory <<'IMMUTABLE_RUNTIME'
#!/bin/sh
exec /opt/igorek/runtime/bin/python -I -m factory.cli "$@"
IMMUTABLE_RUNTIME
chmod 0755 /usr/local/bin/igorek-factory
chown -R root:root /opt/igorek/runtime /opt/igorek/python
chmod -R go-w /opt/igorek/runtime /opt/igorek/python "${IGOREK_GUEST_REPO_DIR}"

# Install the pinned Chrome-for-Testing build into the disposable guest image.
HOME="/home/${IGOREK_GUEST_USER}" agent-browser install --with-deps
chown -R "${IGOREK_GUEST_USER}:${IGOREK_GUEST_USER}" "/home/${IGOREK_GUEST_USER}/.cache"

# Guest unprivileged-userns policy (bwrap needs it; the runtime probe keeps the
# sandbox fail-closed when the policy is not in effect). The kernel log is made
# readable so the agent user can turn an egress denial into a blocked-host trace
# event (plan todo 40b); the guest kernel log holds nothing host-side.
cat > /etc/sysctl.d/90-igorek-userns.conf <<'SYSCTL'
kernel.unprivileged_userns_clone = 1
user.max_user_namespaces = 10000
kernel.dmesg_restrict = 0
SYSCTL
# Ubuntu-derived kernels additionally gate unprivileged userns behind this
# AppArmor knob; Debian kernels lack the key, so it is applied best-effort.
printf '%s\n' "kernel.apparmor_restrict_unprivileged_userns = 0" \
  > /etc/sysctl.d/91-igorek-apparmor-userns.conf
sysctl -p /etc/sysctl.d/90-igorek-userns.conf >/dev/null 2>&1 || true
sysctl -p /etc/sysctl.d/91-igorek-apparmor-userns.conf >/dev/null 2>&1 || true

# bwrap-userns-restrict-class AppArmor profile: unprivileged user namespaces
# for /usr/bin/bwrap only, never a global AppArmor relaxation.
cat > /etc/apparmor.d/bwrap-userns-restrict <<'APPARMOR'
abi <abi/4.0>,
include <tunables/global>
profile bwrap-userns-restrict /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap-userns-restrict>
}
APPARMOR
if command -v apparmor_parser >/dev/null 2>&1; then
  apparmor_parser -r -W /etc/apparmor.d/bwrap-userns-restrict || true
fi

# In-guest readiness probe: same module and same hardened flags the bash tool
# uses, so a passing probe means the sandboxed bash invocation really runs.
cat > /usr/local/bin/igorek-guest-bwrap-probe <<'PROBE'
#!/bin/bash
set -u
export PYTHONPATH="${IGOREK_GUEST_REPO_DIR:-/opt/igorek/app}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m factory.core.isolation.guest_bwrap --probe
PROBE
chmod 0755 /usr/local/bin/igorek-guest-bwrap-probe

# Per-session egress guard (plan todo 40): the boot step applies the rendered
# default-deny nftables ruleset before any network is used, so a session VM can
# never run on the QEMU user-net defaults (including host-loopback 10.0.2.2).
# At boot there is no session allowlist, so the strictest baseline is applied;
# the guest runner replaces it with the host-rendered per-session ruleset that
# arrives with the task, and refuses the turn if that cannot be enforced.
cat > /usr/local/bin/igorek-egress-boot <<'EGRESS_BOOT'
#!/bin/bash
set -u
export PYTHONPATH="${IGOREK_GUEST_REPO_DIR:-/opt/igorek/app}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m factory.core.isolation.egress_enforce --boot-baseline
EGRESS_BOOT
chmod 0755 /usr/local/bin/igorek-egress-boot

# The guest runner is the unprivileged agent user, so it cannot touch the
# netfilter table by itself. It must NOT hold CAP_NET_ADMIN either: bubblewrap
# refuses to run ("Unexpected capabilities but not setuid") when its process
# holds any capability, and the runner's ExecStartPre probe plus every Bash tool
# call go through bwrap. Instead the runner execs this root-owned copy of nft,
# which carries the file capability cap_net_admin+ep; the capability lives only
# in that child, never in the runner process.
install -m 0755 /usr/sbin/nft /usr/local/bin/igorek-nft
setcap cap_net_admin+ep /usr/local/bin/igorek-nft

cat > /etc/systemd/system/igorek-egress-guard.service <<'EGRESS_UNIT'
[Unit]
Description=Igorek isolation egress guard (default-deny before network use)
DefaultDependencies=no
After=network-pre.target
Before=network.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/igorek-egress-boot

[Install]
WantedBy=multi-user.target
EGRESS_UNIT
install -d -m 0755 /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/igorek-egress-guard.service \
  /etc/systemd/system/multi-user.target.wants/igorek-egress-guard.service
systemctl enable igorek-egress-guard.service >/dev/null 2>&1 || true

install -d -m 0755 /etc/systemd/system/systemd-networkd.service.d
cat > /etc/systemd/system/systemd-networkd.service.d/igorek-egress.conf <<'NETWORK_GUARD'
[Unit]
Requires=igorek-egress-guard.service
After=igorek-egress-guard.service
NETWORK_GUARD

# The only host export is mounted before the non-root runner starts. The unit
# requires the mount and the default-deny network baseline; either failure
# prevents task execution instead of silently falling back to the host.
install -d -m 0755 /workspace
printf '%s\n' 'workspace /workspace virtiofs defaults,nofail 0 0' >> /etc/fstab
cat > /etc/systemd/system/igorek-guest-runner.service <<GUEST_UNIT
[Unit]
Description=Igorek isolation guest runner
Requires=workspace.mount igorek-egress-guard.service
After=workspace.mount igorek-egress-guard.service

[Service]
Type=simple
User=${IGOREK_GUEST_USER}
Group=${IGOREK_GUEST_USER}
Environment=HOME=/home/${IGOREK_GUEST_USER}
Environment=PATH=/home/${IGOREK_GUEST_USER}/.local/bin:/usr/local/bin:/usr/bin
Environment=IGOREK_ISOLATION_VSOCK_PORT=5005
Environment=IGOREK_ISOLATION_GUEST=1
Environment=IGOREK_ISOLATION_NFT_BIN=/usr/local/bin/igorek-nft
# MCP server commands are repo-relative (factory/mcp_servers/...); the runner's
# cwd must be the source tree so the guest can start them (the channel policy
# forbids absolute /opt paths on the wire).
WorkingDirectory=${IGOREK_GUEST_REPO_DIR}
ExecStartPre=/usr/local/bin/igorek-guest-bwrap-probe
ExecStart=/usr/local/bin/igorek-factory guest-runner --vsock-cid 2 --vsock-port 5005
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
GUEST_UNIT
ln -sf /etc/systemd/system/igorek-guest-runner.service \
  /etc/systemd/system/multi-user.target.wants/igorek-guest-runner.service
systemctl enable igorek-guest-runner.service >/dev/null 2>&1 || true

# The interactive terminal is a SEPARATE unit on a SEPARATE vsock port (5006):
# the runner is restarted between tasks, while a terminal must survive task
# boundaries; the task channel keeps rejecting shell-named payload keys, so the
# terminal never reuses it. Same hardening shape as the runner: unprivileged
# user, exported workspace as cwd, no capability in the process (the guest
# service drops caps + sets no_new_privs in the child before exec).
cat > /etc/systemd/system/igorek-guest-terminal.service <<GUEST_TERMINAL_UNIT
[Unit]
Description=Igorek isolation guest terminal service (interactive PTY)
Requires=workspace.mount igorek-egress-guard.service
After=workspace.mount igorek-egress-guard.service

[Service]
Type=simple
User=${IGOREK_GUEST_USER}
Group=${IGOREK_GUEST_USER}
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
KillMode=control-group
TimeoutStopSec=5
UMask=0077
Environment=HOME=/home/${IGOREK_GUEST_USER}
Environment=PATH=/home/${IGOREK_GUEST_USER}/.local/bin:/usr/local/bin:/usr/bin
Environment=IGOREK_TERMINAL_WORKDIR=/workspace
WorkingDirectory=${IGOREK_GUEST_REPO_DIR}
ExecStart=/usr/local/bin/igorek-factory guest-terminal --vsock-cid 2 --vsock-port 5006
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
GUEST_TERMINAL_UNIT
install -d -m 0755 /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/igorek-guest-terminal.service \
  /etc/systemd/system/multi-user.target.wants/igorek-guest-terminal.service
systemctl enable igorek-guest-terminal.service >/dev/null 2>&1 || true

# No clone identity, no host state, no secrets in the image.
truncate -s 0 /etc/machine-id || true
rm -f /var/lib/dbus/machine-id
rm -rf "/home/${IGOREK_GUEST_USER}/.cache/uv" "/home/${IGOREK_GUEST_USER}/.git-credentials"
GUEST_SETUP
  chmod 0755 "$target"
}

GUEST_ENV_FILE="$WORK_DIR/guest-env.sh"
GUEST_SETUP_FILE="$WORK_DIR/guest-setup.sh"
render_guest_env "$GUEST_ENV_FILE"
render_guest_setup "$GUEST_SETUP_FILE"

build_with_virt_builder() {
  local curl_retry="$WORK_DIR/curl-retry"
  install -d -m 0755 "$(dirname "$IMAGE_PATH")"
  cat > "$curl_retry" <<'CURL_RETRY'
#!/bin/sh
exec /usr/bin/curl --retry 5 --retry-all-errors --retry-delay 2 "$@"
CURL_RETRY
  chmod 0755 "$curl_retry"
  run_bounded "$BUILD_TIMEOUT_S" virt-builder --curl "$curl_retry" "$DISTRO" \
    --arch "$GUEST_ARCH_X86_64" \
    --format qcow2 \
    --output "$IMAGE_PATH" \
    --size "$DISK_SIZE" \
    --root-password disabled \
    --mkdir /opt/igorek \
    --upload "$GUEST_ENV_FILE:/opt/igorek/guest-env.sh" \
    --upload "$GUEST_SETUP_FILE:/opt/igorek/guest-setup.sh" \
    --upload "$REPO_ARCHIVE:/opt/igorek/repo.tar" \
    --run-command "/bin/bash /opt/igorek/guest-setup.sh"
}

run_in_chroot() { # run_in_chroot <root-dir> <command...>
  local root_dir="$1"
  shift
  local fs
  for fs in proc sys dev; do
    mount --bind "/$fs" "$root_dir/$fs"
  done
  local rc=0
  chroot "$root_dir" "$@" || rc=$?
  for fs in dev sys proc; do
    umount "$root_dir/$fs" || true
  done
  return "$rc"
}

build_with_debootstrap() {
  local root_dir="$WORK_DIR/rootfs" raw_image="$WORK_DIR/rootfs.img"
  install -d -m 0755 "$(dirname "$IMAGE_PATH")"
  run_bounded "$BUILD_TIMEOUT_S" debootstrap --variant=minbase --arch "$GUEST_ARCH_DEB" \
    --include="ca-certificates,curl,git,python3,bubblewrap,apparmor,${KERNEL_PACKAGE}" \
    "$SUITE" "$root_dir" "$MIRROR"
  install -d -m 0755 "$root_dir/opt/igorek"
  install -m 0644 "$GUEST_ENV_FILE" "$root_dir/opt/igorek/guest-env.sh"
  install -m 0755 "$GUEST_SETUP_FILE" "$root_dir/opt/igorek/guest-setup.sh"
  install -m 0644 "$REPO_ARCHIVE" "$root_dir/opt/igorek/repo.tar"
  run_bounded "$BUILD_TIMEOUT_S" run_in_chroot "$root_dir" /opt/igorek/guest-setup.sh
  run_bounded "$BUILD_TIMEOUT_S" mkfs.ext4 -F -d "$root_dir" "$raw_image" "$RAW_SIZE"
  run_bounded "$BUILD_TIMEOUT_S" qemu-img convert -f raw -O qcow2 "$raw_image" "$IMAGE_PATH"
}

build_with_template() {
  [[ -n "$TEMPLATE_IMAGE" && -f "$TEMPLATE_IMAGE" ]] || fail "template image is missing"
  [[ "$TEMPLATE_IMAGE" != "$IMAGE_PATH" ]] || fail "template image must differ from output image"
  install -d -m 0755 "$(dirname "$IMAGE_PATH")"
  run_bounded "$BUILD_TIMEOUT_S" qemu-img convert -p -O qcow2 "$TEMPLATE_IMAGE" "$IMAGE_PATH"
  run_bounded "$BUILD_TIMEOUT_S" virt-customize --network -a "$IMAGE_PATH" \
    --mkdir /opt/igorek \
    --upload "$GUEST_ENV_FILE:/opt/igorek/guest-env.sh" \
    --upload "$GUEST_SETUP_FILE:/opt/igorek/guest-setup.sh" \
    --upload "$REPO_ARCHIVE:/opt/igorek/repo.tar" \
    --run-command "/bin/bash /opt/igorek/guest-setup.sh"
}

if [[ -e "$IMAGE_PATH" || -e "${IMAGE_PATH}.manifest.json" ]]; then
  if [[ "$FORCE" != "1" ]]; then
    fail "image or manifest already exists: $IMAGE_PATH (use --force to rebuild)"
  fi
  rm -f "$IMAGE_PATH" "${IMAGE_PATH}.manifest.json"
fi

case "$BUILDER" in
  auto)
    if have virt-builder && have virt-customize && have qemu-img; then
      log "BUILD: virt-builder $DISTRO -> $IMAGE_PATH (timeout ${BUILD_TIMEOUT_S}s)"
      build_with_virt_builder
    else
      log "BUILD: debootstrap $SUITE -> $IMAGE_PATH (timeout ${BUILD_TIMEOUT_S}s)"
      build_with_debootstrap
    fi
    ;;
  virt-builder)
    have virt-builder && have virt-customize && have qemu-img || fail "virt-builder backend is unavailable"
    log "BUILD: virt-builder $DISTRO -> $IMAGE_PATH (timeout ${BUILD_TIMEOUT_S}s)"
    build_with_virt_builder
    ;;
  debootstrap)
    have debootstrap && have mkfs.ext4 && have qemu-img || fail "debootstrap backend is unavailable"
    log "BUILD: debootstrap $SUITE -> $IMAGE_PATH (timeout ${BUILD_TIMEOUT_S}s)"
    build_with_debootstrap
    ;;
  template)
    have virt-customize && have qemu-img || fail "template backend is unavailable"
    log "BUILD: customize $TEMPLATE_IMAGE -> $IMAGE_PATH (timeout ${BUILD_TIMEOUT_S}s)"
    build_with_template
    ;;
  *) fail "unknown image builder: $BUILDER (expected auto, virt-builder, debootstrap, or template)" ;;
esac

if [[ ! -f "$IMAGE_PATH" ]]; then
  fail "build finished without producing $IMAGE_PATH"
fi

MANIFEST_ARGS=(manifest --image "$IMAGE_PATH" --repo-commit "$REPO_COMMIT" --userns-policy "$USERNS_POLICY_RECORD")
if [[ -n "$TOOL_VERSION" ]]; then
  MANIFEST_ARGS+=(--tool-version "$TOOL_VERSION")
fi
manifest_tool "${MANIFEST_ARGS[@]}"

if [[ -z "$EXPECTED_TOOL_VERSION" ]]; then
  EXPECTED_TOOL_VERSION="$(resolved_tool_version | tr -d '[:space:]')"
fi
run_verify
log "PASS: base image built and verified: $IMAGE_PATH"
