"""Current Codex CLI version (port of codex-rs/tui/src/version.rs).

In the Python port the version string is sourced from the installed
package metadata rather than the Cargo compile-time ``CARGO_PKG_VERSION``.
"""
from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version
    CODEX_CLI_VERSION: str = _pkg_version("factory")
except Exception:
    CODEX_CLI_VERSION = "0.0.0"


__all__ = ["CODEX_CLI_VERSION"]
