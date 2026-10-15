"""Clipboard copy backend (port of codex-rs/tui/src/clipboard_copy.rs).

The Python port follows the same selection order as the Rust source:

* SSH session: tmux clipboard (when available) → OSC 52
* Local session: arboard (native) → WSL PowerShell → tmux/OSC 52

The test module from the source is intentionally **not** ported per the
project's "no tests" policy.
"""
from __future__ import annotations

import base64
import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import contextlib


OSC52_MAX_RAW_BYTES: int = 100_000


# ---------------------------------------------------------------------------
# Clipboard lease (port of `ClipboardLease`)
# ---------------------------------------------------------------------------

class ClipboardLease:
    """Keeps a platform clipboard owner alive when the backend requires one.

    On Linux/X11 and some Wayland setups, clipboard contents are served by
    the owning process; store this lease for the lifetime of the TUI.

    On other platforms the lease is a no-op handle.
    """

    _NATIVE_BACKEND_ATTR = "_clipboard_backend"

    def __init__(self) -> None:
        self._clipboard_backend: Optional[object] = None

    @classmethod
    def native_linux(cls, backend: object) -> "ClipboardLease":
        lease = cls()
        lease._clipboard_backend = backend
        return lease

    @classmethod
    def test(cls) -> "ClipboardLease":
        return cls()

    @classmethod
    def process_lease(cls, process: object) -> "ClipboardLease":
        """Own an external process that serves the clipboard (X11/Wayland)."""
        lease = cls()
        lease._clipboard_backend = process
        return lease


# ---------------------------------------------------------------------------
# Environment detection (port of `is_ssh_session` / `is_tmux_session` / `is_wsl_session`)
# ---------------------------------------------------------------------------

def is_ssh_session() -> bool:
    return "SSH_TTY" in os.environ or "SSH_CONNECTION" in os.environ


def is_tmux_session() -> bool:
    return "TMUX" in os.environ or "TMUX_PANE" in os.environ


def is_wsl_session() -> bool:
    if sys.platform != "linux":
        return False
    try:
        from factory.core.tui.clipboard_paste import _is_probably_wsl
        return _is_probably_wsl()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Stderr suppression (port of `SuppressStderr`)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _suppress_stderr():
    """Redirect fd 2 to /dev/null on macOS; no-op elsewhere.

    Port of ``SuppressStderr``. On macOS, arboard's NSPasteboard init writes
    to stderr and corrupts the TUI display, hence the redirect. On Linux/Windows
    we keep stderr intact.
    """
    if sys.platform != "darwin":
        yield
        return
    try:
        saved = os.dup(2)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 2)
        os.close(devnull_fd)
    except OSError:
        try:
            yield
        finally:
            pass
        return
    try:
        yield
    finally:
        try:
            os.dup2(saved, 2)
            os.close(saved)
        except OSError:
            pass


_STDERR_SUPPRESSION_MUTEX = threading.Lock()


# ---------------------------------------------------------------------------
# arboard backend
# ---------------------------------------------------------------------------

def arboard_copy(text: str) -> Tuple[Optional[ClipboardLease], Optional[str]]:
    """Copy ``text`` through the native clipboard (port of ``arboard_copy``)."""
    if sys.platform == "android":
        return None, "native clipboard unavailable on Android"
    try:
        from factory.core.tui.clipboard_paste import _is_probably_wsl  # noqa: F401
    except Exception:
        pass
    try:
        with _STDERR_SUPPRESSION_MUTEX, _suppress_stderr():
            import arboard  # type: ignore
    except Exception as exc:
        return None, f"clipboard unavailable: {exc}"
    try:
        with _STDERR_SUPPRESSION_MUTEX, _suppress_stderr():
            clipboard = arboard.Clipboard()
            clipboard.set_text(text)
    except Exception as exc:
        return None, f"failed to set clipboard text: {exc}"
    if sys.platform == "linux":
        return ClipboardLease.native_linux(clipboard), None
    return None, None


# ---------------------------------------------------------------------------
# WSL PowerShell fallback
# ---------------------------------------------------------------------------

def wsl_clipboard_copy(text: str) -> Optional[str]:
    """Copy ``text`` into the Windows clipboard from a WSL process."""
    if sys.platform != "linux":
        return "WSL clipboard fallback unavailable on this platform"
    import subprocess
    try:
        child = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
                "$ErrorActionPreference = 'Stop'; "
                "$text = [Console]::In.ReadToEnd(); "
                "Set-Clipboard -Value $text",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        return f"failed to spawn powershell.exe: {exc}"
    if child.stdin is None:
        child.kill()
        child.wait()
        return "failed to open powershell.exe stdin"
    try:
        child.stdin.write(text.encode("utf-8"))
        child.stdin.close()
    except OSError as exc:
        child.kill()
        child.wait()
        return f"failed to write to powershell.exe: {exc}"
    try:
        output = child.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        return "powershell.exe timed out"
    if child.returncode == 0:
        return None
    stderr = (output[1] or b"").decode("utf-8", errors="replace").strip()
    if not stderr:
        return f"powershell.exe exited with status {child.returncode}"
    return f"powershell.exe failed: {stderr}"


# ---------------------------------------------------------------------------
# tmux backend
# ---------------------------------------------------------------------------

def tmux_clipboard_copy(text: str) -> Optional[str]:
    """Copy ``text`` through tmux's native clipboard (port of ``tmux_clipboard_copy``)."""
    ready = tmux_clipboard_copy_ready(
        lambda: tmux_command_output(["show-options", "-gv", "set-clipboard"]),
        lambda: tmux_command_output(["info"]),
    )
    if ready is not None:
        return ready
    import subprocess
    try:
        child = subprocess.Popen(
            ["tmux", "load-buffer", "-w", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        return f"failed to spawn tmux: {exc}"
    if child.stdin is None:
        child.kill()
        child.wait()
        return "failed to open tmux stdin"
    try:
        child.stdin.write(text.encode("utf-8"))
        child.stdin.close()
    except OSError as exc:
        child.kill()
        child.wait()
        return f"failed to write to tmux: {exc}"
    try:
        output = child.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        return "tmux timed out"
    if child.returncode == 0:
        return None
    stderr = (output[1] or b"").decode("utf-8", errors="replace").strip()
    if not stderr:
        return f"tmux exited with status {child.returncode}"
    return f"tmux failed: {stderr}"


def tmux_clipboard_copy_ready(
    set_clipboard_fn: Callable[[], Optional[str]],
    tmux_info_fn: Callable[[], Optional[str]],
) -> Optional[str]:
    """Verify that tmux is configured to forward clipboard writes."""
    set_clipboard = set_clipboard_fn()
    if set_clipboard is not None and set_clipboard.strip() == "off":
        return "tmux clipboard forwarding is disabled"
    tmux_info = tmux_info_fn()
    if tmux_info is not None and any("Ms: [missing]" in line for line in tmux_info.splitlines()):
        return "tmux clipboard forwarding is unavailable: missing Ms capability"
    return None


def tmux_command_output(args) -> Optional[str]:
    """Run ``tmux <args>`` and return its stdout (port of ``tmux_command_output``)."""
    import subprocess
    try:
        output = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            check=False,
        )
    except (OSError, FileNotFoundError) as exc:
        return f"failed to spawn tmux: {exc}"
    if output.returncode == 0:
        return output.stdout.decode("utf-8", errors="replace")
    stderr = (output.stderr or b"").decode("utf-8", errors="replace").strip()
    if not stderr:
        return f"tmux exited with status {output.returncode}"
    return f"tmux failed: {stderr}"


# ---------------------------------------------------------------------------
# OSC 52 backend
# ---------------------------------------------------------------------------

def osc52_sequence(text: str, tmux: bool) -> Tuple[Optional[str], Optional[str]]:
    """Build the OSC 52 escape sequence (port of ``osc52_sequence``)."""
    raw_bytes = len(text.encode("utf-8"))
    if raw_bytes > OSC52_MAX_RAW_BYTES:
        return None, (
            f"OSC 52 payload too large ({raw_bytes} bytes; max {OSC52_MAX_RAW_BYTES})"
        )
    encoded = base64.standard_b64encode(text.encode("utf-8")).decode("ascii")
    if tmux:
        return f"\x1bPtmux;\x1b\x1b]52;c;{encoded}\x07\x1b\\", None
    return f"\x1b]52;c;{encoded}\x07", None


def write_osc52_to_writer(sequence: str, writer) -> Optional[str]:
    """Write ``sequence`` to ``writer`` (port of ``write_osc52_to_writer``)."""
    try:
        writer.write(sequence.encode("utf-8"))
        writer.flush()
    except (OSError, ValueError) as exc:
        return f"failed to write OSC 52: {exc}"
    return None


def osc52_copy(text: str) -> Optional[str]:
    """Write ``text`` to the clipboard via the OSC 52 escape sequence."""
    sequence, err = osc52_sequence(text, "TMUX" in os.environ)
    if err is not None:
        return err
    if sys.platform != "win32":
        try:
            with open("/dev/tty", "wb") as tty:
                result = write_osc52_to_writer(sequence, tty)
                if result is None:
                    return None
        except (OSError, ValueError):
            pass
    try:
        return write_osc52_to_writer(sequence, sys.stdout)
    except (OSError, ValueError) as exc:
        return f"failed to write OSC 52: {exc}"


# ---------------------------------------------------------------------------
# Linux command-line backends (wl-copy / xclip / xsel)
# ---------------------------------------------------------------------------

_COPY_TIMEOUT_SECONDS = 2.0


def _command_available(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _run_pipe_command(
    args: list[str],
    text: str,
    *,
    timeout: float = _COPY_TIMEOUT_SECONDS,
    wait: bool = True,
) -> Tuple[Optional[object], Optional[str]]:
    """Run ``args`` feeding ``text`` through stdin, without a shell.

    Returns ``(process, error)``. When ``wait`` is False the process is
    returned still running (for clipboard owners that must stay alive) and
    it is the caller's responsibility to keep it referenced.
    """
    import subprocess
    try:
        child = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        return None, f"failed to spawn {args[0]}: {exc}"
    if child.stdin is None:
        child.kill()
        try:
            child.wait(timeout=timeout)
        except Exception:
            pass
        return None, f"failed to open {args[0]} stdin"

    if not wait:
        try:
            child.stdin.write(text.encode("utf-8"))
            child.stdin.flush()
            child.stdin.close()
        except OSError as exc:
            child.kill()
            try:
                child.wait(timeout=timeout)
            except Exception:
                pass
            return None, f"failed to write to {args[0]}: {exc}"
        return child, None

    # Let communicate() own the stdin lifecycle. Closing stdin manually and
    # then calling communicate() raises ``ValueError: flush of closed file``.
    try:
        _out, err = child.communicate(input=text.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        try:
            child.wait(timeout=timeout)
        except Exception:
            pass
        # xclip/xsel stay resident to serve the selection; a timeout here
        # is not a failure, but these backends use wait=False instead.
        return None, f"{args[0]} timed out"
    if child.returncode == 0:
        return child, None
    stderr = (err or b"").decode("utf-8", errors="replace").strip()
    if not stderr:
        return None, f"{args[0]} exited with status {child.returncode}"
    return None, f"{args[0]} failed: {stderr}"


def wl_clipboard_copy(text: str) -> Tuple[Optional[ClipboardLease], Optional[str]]:
    """Copy via ``wl-copy`` on a Wayland session."""
    if not _command_available("wl-copy"):
        return None, "wl-copy not found"
    proc, err = _run_pipe_command(["wl-copy", "--type", "text/plain"], text, wait=True)
    if err is not None:
        return None, err
    # wl-copy forks a background owner and exits; keep a lease anyway.
    return ClipboardLease.process_lease(proc), None


def xclip_clipboard_copy(text: str) -> Tuple[Optional[ClipboardLease], Optional[str]]:
    """Copy via ``xclip -selection clipboard`` on X11."""
    if not _command_available("xclip"):
        return None, "xclip not found"
    proc, err = _run_pipe_command(
        ["xclip", "-selection", "clipboard", "-in"], text, wait=False
    )
    if err is not None:
        return None, err
    return ClipboardLease.process_lease(proc), None


def xsel_clipboard_copy(text: str) -> Tuple[Optional[ClipboardLease], Optional[str]]:
    """Copy via ``xsel --clipboard --input`` on X11."""
    if not _command_available("xsel"):
        return None, "xsel not found"
    proc, err = _run_pipe_command(
        ["xsel", "--clipboard", "--input"], text, wait=False
    )
    if err is not None:
        return None, err
    return ClipboardLease.process_lease(proc), None


def linux_clipboard_copy(
    text: str,
    *,
    wayland: Optional[bool] = None,
    x11: Optional[bool] = None,
) -> Tuple[Optional[ClipboardLease], Optional[str], Optional[str]]:
    """Try Linux clipboard owners in order.

    Wayland (``wl-copy``) is preferred when a Wayland session is detected;
    otherwise the X11 owners (``xclip``, ``xsel``) are tried. Returns
    ``(lease, error, backend_label)``.
    """
    import os
    if wayland is None:
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        )
    if x11 is None:
        x11 = bool(os.environ.get("DISPLAY"))
    candidates: list[Tuple[str, Callable[[str], Tuple[Optional[ClipboardLease], Optional[str]]]]] = []
    if wayland:
        candidates.append(("wl-copy", wl_clipboard_copy))
    if x11:
        candidates.append(("xclip", xclip_clipboard_copy))
        candidates.append(("xsel", xsel_clipboard_copy))
    if not wayland and not x11:
        # No display detected: still attempt wl-copy, which may use a
        # wayland socket proxy, then X11 tools if present.
        candidates.append(("wl-copy", wl_clipboard_copy))
        candidates.append(("xclip", xclip_clipboard_copy))
        candidates.append(("xsel", xsel_clipboard_copy))
    errors: list[str] = []
    for label, fn in candidates:
        lease, err = fn(text)
        if err is None:
            return lease, None, label
        errors.append(f"{label}: {err}")
    if not errors:
        return None, "no Linux clipboard backend available", None
    return None, "; ".join(errors), None


# ---------------------------------------------------------------------------
# Public entry point (port of `copy_to_clipboard` / `copy_to_clipboard_with`)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CopyEnvironment:
    ssh_session: bool
    wsl_session: bool
    tmux_session: bool


def copy_to_clipboard(text: str) -> Tuple[Optional[ClipboardLease], Optional[str]]:
    """Copy ``text`` to the system clipboard (port of ``copy_to_clipboard``)."""
    lease, err, _backend = copy_to_clipboard_detailed(text)
    return lease, err


def copy_to_clipboard_detailed(
    text: str,
) -> Tuple[Optional[ClipboardLease], Optional[str], Optional[str]]:
    """Copy text and report ``(lease, error, backend_label)``."""
    return _copy_detailed(
        text,
        CopyEnvironment(
            ssh_session=is_ssh_session(),
            wsl_session=is_wsl_session(),
            tmux_session=is_tmux_session(),
        ),
        tmux_clipboard_copy,
        osc52_copy,
        arboard_copy,
        wsl_clipboard_copy,
        linux_clipboard_copy,
    )


def copy_to_clipboard_with(
    text: str,
    environment: CopyEnvironment,
    tmux_copy_fn: Callable[[str], Optional[str]],
    osc52_copy_fn: Callable[[str], Optional[str]],
    arboard_copy_fn: Callable[[str], Tuple[Optional[ClipboardLease], Optional[str]]],
    wsl_copy_fn: Callable[[str], Optional[str]],
    linux_copy_fn: Optional[
        Callable[[str], Tuple[Optional[ClipboardLease], Optional[str], Optional[str]]]
    ] = None,
) -> Tuple[Optional[ClipboardLease], Optional[str]]:
    """Core copy logic with injected backends (port one-to-one)."""
    lease, err, _backend = _copy_detailed(
        text,
        environment,
        tmux_copy_fn,
        osc52_copy_fn,
        arboard_copy_fn,
        wsl_copy_fn,
        linux_copy_fn,
    )
    return lease, err


def _copy_detailed(
    text: str,
    environment: CopyEnvironment,
    tmux_copy_fn: Callable[[str], Optional[str]],
    osc52_copy_fn: Callable[[str], Optional[str]],
    arboard_copy_fn: Callable[[str], Tuple[Optional[ClipboardLease], Optional[str]]],
    wsl_copy_fn: Callable[[str], Optional[str]],
    linux_copy_fn: Optional[
        Callable[[str], Tuple[Optional[ClipboardLease], Optional[str], Optional[str]]]
    ] = None,
) -> Tuple[Optional[ClipboardLease], Optional[str], Optional[str]]:
    if environment.ssh_session:
        terminal_err = terminal_clipboard_copy_with(
            text, environment.tmux_session, tmux_copy_fn, osc52_copy_fn
        )
        if terminal_err is not None:
            prefix = (
                "terminal clipboard copy failed over SSH"
                if environment.tmux_session
                else "OSC 52 clipboard copy failed over SSH"
            )
            return None, f"{prefix}: {terminal_err}", None
        backend = "tmux" if environment.tmux_session else "osc52"
        return None, None, backend

    native_lease, native_err = arboard_copy_fn(text)
    if native_err is None:
        return native_lease, None, "arboard"

    if environment.wsl_session:
        wsl_err = wsl_copy_fn(text)
        if wsl_err is None:
            return None, None, "wsl"
        terminal_err = terminal_clipboard_copy_with(
            text, environment.tmux_session, tmux_copy_fn, osc52_copy_fn
        )
        if terminal_err is not None:
            terminal_label = "terminal fallback" if environment.tmux_session else "OSC 52 fallback"
            return None, (
                f"native clipboard: {native_err}; "
                f"WSL fallback: {wsl_err}; "
                f"{terminal_label}: {terminal_err}"
            ), None
        backend = "tmux" if environment.tmux_session else "osc52"
        return None, None, backend

    # Linux native owners (wl-copy / xclip / xsel) before terminal escape.
    if linux_copy_fn is not None:
        linux_lease, linux_err, linux_label = linux_copy_fn(text)
        if linux_err is None:
            return linux_lease, None, linux_label
        native_err = f"{native_err}; {linux_err}"

    terminal_err = terminal_clipboard_copy_with(
        text, environment.tmux_session, tmux_copy_fn, osc52_copy_fn
    )
    if terminal_err is not None:
        terminal_label = "terminal fallback" if environment.tmux_session else "OSC 52 fallback"
        return None, f"native clipboard: {native_err}; {terminal_label}: {terminal_err}", None
    backend = "tmux" if environment.tmux_session else "osc52"
    return None, None, backend



def terminal_clipboard_copy_with(
    text: str,
    tmux_session: bool,
    tmux_copy_fn: Callable[[str], Optional[str]],
    osc52_copy_fn: Callable[[str], Optional[str]],
) -> Optional[str]:
    """Copy through the active terminal (port of ``terminal_clipboard_copy_with``)."""
    if tmux_session:
        tmux_err = tmux_copy_fn(text)
        if tmux_err is None:
            return None
        osc_err = osc52_copy_fn(text)
        if osc_err is None:
            return None
        return f"tmux clipboard: {tmux_err}; OSC 52 fallback: {osc_err}"
    return osc52_copy_fn(text)


__all__ = [
    "ClipboardLease",
    "CopyEnvironment",
    "OSC52_MAX_RAW_BYTES",
    "arboard_copy",
    "copy_to_clipboard",
    "copy_to_clipboard_detailed",
    "copy_to_clipboard_with",
    "is_ssh_session",
    "is_tmux_session",
    "is_wsl_session",
    "linux_clipboard_copy",
    "osc52_copy",
    "osc52_sequence",
    "terminal_clipboard_copy_with",
    "tmux_clipboard_copy",
    "tmux_clipboard_copy_ready",
    "tmux_command_output",
    "wl_clipboard_copy",
    "wsl_clipboard_copy",
    "write_osc52_to_writer",
    "xclip_clipboard_copy",
    "xsel_clipboard_copy",
]
