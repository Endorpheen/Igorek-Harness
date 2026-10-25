"""Clipboard transports are tested without changing the user's clipboard."""

import contextlib
import io
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.tui import clipboard_copy as c


@pytest.fixture(autouse=True)
def no_real_clipboard(monkeypatch):
    original_popen = subprocess.Popen

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected clipboard command or terminal write")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(c, "open", forbidden, raising=False)
    fake = ModuleType("arboard")
    fake.Clipboard = Mock(
        side_effect=RuntimeError("fixture native clipboard unavailable")
    )
    monkeypatch.setitem(sys.modules, "arboard", fake)
    for name in (
        "SSH_TTY",
        "SSH_CONNECTION",
        "TMUX",
        "TMUX_PANE",
        "WAYLAND_DISPLAY",
        "XDG_SESSION_TYPE",
        "DISPLAY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        c, "sys", SimpleNamespace(platform="linux", stdout=io.BytesIO())
    )
    return original_popen


def child(returncode=0, error=b""):
    return SimpleNamespace(
        stdin=Mock(),
        communicate=Mock(return_value=(b"", error)),
        kill=Mock(),
        wait=Mock(),
        returncode=returncode,
    )


@pytest.mark.parametrize(
    "name,detect",
    [
        ("SSH_TTY", c.is_ssh_session),
        ("SSH_CONNECTION", c.is_ssh_session),
        ("TMUX", c.is_tmux_session),
        ("TMUX_PANE", c.is_tmux_session),
    ],
)
def test_remote_environment_keys_are_detected_even_when_empty(
    monkeypatch, name, detect
):
    assert not detect()
    monkeypatch.setenv(name, "")
    assert detect()


@pytest.mark.parametrize(
    "platform,detected",
    [("linux", True), ("linux", False), ("win32", False), ("darwin", False)],
)
def test_wsl_detection_delegates_only_on_linux(monkeypatch, platform, detected):
    from factory.core.tui import clipboard_paste

    probe = Mock(return_value=detected)
    monkeypatch.setattr(clipboard_paste, "_is_probably_wsl", probe)
    c.sys.platform = platform
    assert c.is_wsl_session() is detected
    assert probe.call_count == int(platform == "linux")


def test_wsl_probe_failure_is_safe(monkeypatch):
    from factory.core.tui import clipboard_paste

    monkeypatch.setattr(
        clipboard_paste,
        "_is_probably_wsl",
        Mock(side_effect=OSError("fixture probe failed")),
    )
    assert not c.is_wsl_session()


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_native_backend_passes_unicode_and_keeps_linux_owner_reference(
    monkeypatch, platform
):
    clipboard = SimpleNamespace(set_text=Mock())
    fake = ModuleType("arboard")
    fake.Clipboard = Mock(return_value=clipboard)
    monkeypatch.setitem(sys.modules, "arboard", fake)
    monkeypatch.setattr(c, "_suppress_stderr", contextlib.nullcontext)
    c.sys.platform = platform
    lease, error = c.arboard_copy("Привет 🐙\nsecond line")
    assert error is None
    clipboard.set_text.assert_called_once_with("Привет 🐙\nsecond line")
    if platform == "linux":
        assert lease._clipboard_backend is clipboard
    else:
        assert lease is None


@pytest.mark.parametrize("failure", ["import", "create", "set"])
def test_native_backend_errors_are_reported(monkeypatch, failure):
    fake = ModuleType("arboard")
    fake.Clipboard = Mock(
        return_value=SimpleNamespace(
            set_text=Mock(side_effect=RuntimeError("fixture set failed"))
        )
    )
    if failure == "import":
        monkeypatch.setitem(sys.modules, "arboard", None)
    else:
        if failure == "create":
            fake.Clipboard.side_effect = RuntimeError("fixture init failed")
        monkeypatch.setitem(sys.modules, "arboard", fake)
    lease, error = c.arboard_copy("fixture")
    assert lease is None and error
    assert (
        "unavailable" if failure == "import" else "failed to set clipboard"
    ) in error


def test_android_returns_without_loading_native_clipboard(monkeypatch):
    c.sys.platform = "android"
    assert c.arboard_copy("fixture") == (
        None,
        "native clipboard unavailable on Android",
    )


@pytest.mark.parametrize("phase", ["success", "setup", "restore"])
def test_macos_stderr_suppression_restores_owned_descriptors(monkeypatch, phase):
    fake = SimpleNamespace(
        devnull="/fixture/null",
        O_WRONLY=1,
        dup=Mock(return_value=9),
        open=Mock(return_value=10),
        dup2=Mock(),
        close=Mock(),
    )
    if phase == "setup":
        fake.dup.side_effect = OSError("fixture dup unavailable")
    if phase == "restore":
        fake.dup2.side_effect = [None, OSError("fixture restore unavailable")]
    monkeypatch.setattr(c, "os", fake)
    c.sys.platform = "darwin"
    with c._suppress_stderr():
        reached = True
    assert reached
    if phase == "setup":
        fake.open.assert_not_called()
    else:
        assert fake.dup2.call_args_list[0].args == (10, 2)
        assert fake.dup2.call_args_list[1].args == (9, 2)
        assert fake.close.call_args_list[0].args == (10,)


@pytest.mark.parametrize("wait", [False, True])
def test_pipe_unicode_is_delivered_once_without_shell_or_closed_stdin(
    monkeypatch, wait
):
    process = child()
    popen = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", popen)
    result, error = c._run_pipe_command(
        ["fixture-copy", "--text"], "Привет 🐙", timeout=1.25, wait=wait
    )
    assert result is process and error is None
    assert popen.call_args.args == (["fixture-copy", "--text"],)
    assert popen.call_args.kwargs == {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
    }
    if wait:
        process.communicate.assert_called_once_with(
            input="Привет 🐙".encode(), timeout=1.25
        )
        process.stdin.write.assert_not_called()
        process.stdin.close.assert_not_called()
    else:
        process.communicate.assert_not_called()
        process.stdin.write.assert_called_once_with("Привет 🐙".encode())
        process.stdin.flush.assert_called_once_with()
        process.stdin.close.assert_called_once_with()


@pytest.mark.parametrize("wait", [False, True])
def test_pipe_spawn_failure_returns_detail(monkeypatch, wait):
    monkeypatch.setattr(
        subprocess, "Popen", Mock(side_effect=OSError("fixture missing executable"))
    )
    process, error = c._run_pipe_command(["fixture-copy"], "text", wait=wait)
    assert process is None and "fixture missing executable" in error


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_pipe_missing_stdin_kills_and_reaps_child(monkeypatch, cleanup_fails):
    process = child()
    process.stdin = None
    if cleanup_fails:
        process.wait.side_effect = OSError("fixture already gone")
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    assert c._run_pipe_command(["fixture-copy"], "text") == (
        None,
        "failed to open fixture-copy stdin",
    )
    process.kill.assert_called_once_with()
    process.wait.assert_called_once()


@pytest.mark.parametrize("operation", ["write", "flush", "close"])
def test_resident_pipe_write_errors_stop_owned_child(monkeypatch, operation):
    process = child()
    getattr(process.stdin, operation).side_effect = OSError("fixture pipe failed")
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    result, error = c._run_pipe_command(["fixture-copy"], "text", wait=False)
    assert result is None and "fixture pipe failed" in error
    process.kill.assert_called_once_with()
    process.wait.assert_called_once()


def test_waiting_pipe_timeout_kills_and_reaps_child(monkeypatch):
    process = child()
    process.communicate.side_effect = subprocess.TimeoutExpired("fixture-copy", 0.1)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    assert c._run_pipe_command(["fixture-copy"], "text", timeout=0.1) == (
        None,
        "fixture-copy timed out",
    )
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=0.1)


@pytest.mark.parametrize(
    "error,expected",
    [
        (b"fixture denied", "failed: fixture denied"),
        (b"", "exited with status 7"),
        (b"invalid \xff", "failed: invalid �"),
    ],
)
def test_pipe_nonzero_exit_preserves_decoded_error(monkeypatch, error, expected):
    process = child(7, error)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    result, message = c._run_pipe_command(["fixture-copy"], "text")
    assert result is None and expected in message


@pytest.mark.parametrize(
    "function,name,args,wait",
    [
        (c.wl_clipboard_copy, "wl-copy", ["wl-copy", "--type", "text/plain"], True),
        (
            c.xclip_clipboard_copy,
            "xclip",
            ["xclip", "-selection", "clipboard", "-in"],
            False,
        ),
        (c.xsel_clipboard_copy, "xsel", ["xsel", "--clipboard", "--input"], False),
    ],
)
@pytest.mark.parametrize("outcome", ["missing", "error", "ok"])
def test_linux_command_wrappers_preserve_owner_lease_and_arguments(
    monkeypatch, function, name, args, wait, outcome
):
    process = object()
    monkeypatch.setattr(c, "_command_available", lambda command: outcome != "missing")
    run = Mock(return_value=(process, "fixture denied" if outcome == "error" else None))
    monkeypatch.setattr(c, "_run_pipe_command", run)
    lease, error = function("fixture")
    if outcome == "missing":
        assert lease is None and error == name + " not found"
        run.assert_not_called()
    elif outcome == "error":
        assert lease is None and error == "fixture denied"
    else:
        assert error is None and lease._clipboard_backend is process
    if outcome != "missing":
        run.assert_called_once_with(args, "fixture", wait=wait)


@pytest.mark.parametrize(
    "wayland,x11,working,expected_calls,label",
    [
        (True, True, "wl-copy", ["wl-copy"], "wl-copy"),
        (True, True, "xclip", ["wl-copy", "xclip"], "xclip"),
        (False, True, "xsel", ["xclip", "xsel"], "xsel"),
        (False, False, "xsel", ["wl-copy", "xclip", "xsel"], "xsel"),
        (True, False, "none", ["wl-copy"], None),
    ],
)
def test_linux_selection_stops_at_first_working_owner(
    monkeypatch, wayland, x11, working, expected_calls, label
):
    calls = []
    lease = c.ClipboardLease.test()

    def backend(name):
        def copy(text):
            assert text == "fixture"
            calls.append(name)
            return (lease, None) if name == working else (None, name + " unavailable")

        return copy

    for name, function in [
        ("wl-copy", "wl_clipboard_copy"),
        ("xclip", "xclip_clipboard_copy"),
        ("xsel", "xsel_clipboard_copy"),
    ]:
        monkeypatch.setattr(c, function, backend(name))
    actual_lease, error, actual_label = c.linux_clipboard_copy(
        "fixture", wayland=wayland, x11=x11
    )
    assert calls == expected_calls and actual_label == label
    if label:
        assert actual_lease is lease and error is None
    else:
        assert actual_lease is None and "wl-copy unavailable" in error


@pytest.mark.parametrize(
    "variable,value,expected",
    [
        ("WAYLAND_DISPLAY", "wayland-0", "wl-copy"),
        ("XDG_SESSION_TYPE", "Wayland", "wl-copy"),
        ("DISPLAY", ":fixture", "xclip"),
    ],
)
def test_linux_environment_selects_native_display_family(
    monkeypatch, variable, value, expected
):
    monkeypatch.setenv(variable, value)
    for name, function in [
        ("wl-copy", "wl_clipboard_copy"),
        ("xclip", "xclip_clipboard_copy"),
        ("xsel", "xsel_clipboard_copy"),
    ]:
        monkeypatch.setattr(
            c, function, Mock(return_value=(c.ClipboardLease.test(), None))
        )
    assert c.linux_clipboard_copy("fixture")[2] == expected


@pytest.mark.parametrize("function", [c.wsl_clipboard_copy, c.tmux_clipboard_copy])
def test_windows_and_tmux_writers_work_with_real_owned_python_pipe(
    tmp_path, monkeypatch, no_real_clipboard, function
):
    original_popen = no_real_clipboard
    processes = []
    output = tmp_path / "copied-by-fixture.txt"
    if function is c.tmux_clipboard_copy:
        monkeypatch.setattr(c, "tmux_clipboard_copy_ready", lambda *args: None)

    def start(args, **kwargs):
        assert args[0] in {"powershell.exe", "tmux"}
        process = original_popen(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
                str(output),
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    try:
        assert function("Привет 🐙\nfixture") is None
        assert output.read_text() == "Привет 🐙\nfixture"
        assert all(process.poll() == 0 for process in processes)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


@pytest.mark.parametrize("function", [c.wsl_clipboard_copy, c.tmux_clipboard_copy])
@pytest.mark.parametrize(
    "failure", ["spawn", "stdin", "write", "timeout", "exit", "exit-empty", "ok"]
)
def test_windows_and_tmux_transport_error_contracts(monkeypatch, function, failure):
    if function is c.tmux_clipboard_copy:
        monkeypatch.setattr(c, "tmux_clipboard_copy_ready", lambda *args: None)
    process = child(
        returncode=3 if failure.startswith("exit") else 0,
        error=b"fixture failed" if failure == "exit" else b"",
    )
    popen = Mock(return_value=process)
    if failure == "spawn":
        popen.side_effect = OSError("fixture command missing")
    elif failure == "stdin":
        process.stdin = None
    elif failure == "write":
        process.communicate.side_effect = OSError("fixture broken pipe")
    elif failure == "timeout":
        process.communicate.side_effect = subprocess.TimeoutExpired("fixture", 30)
    monkeypatch.setattr(subprocess, "Popen", popen)
    error = function("Привет")
    if failure == "ok":
        assert error is None
        process.communicate.assert_called_once()
        supplied = process.communicate.call_args.kwargs.get("input")
        if supplied is None:
            process.stdin.write.assert_called_once_with("Привет".encode())
        else:
            assert supplied == "Привет".encode()
            process.stdin.write.assert_not_called()
    else:
        assert error
        expected = {
            "spawn": "fixture command missing",
            "stdin": "stdin",
            "write": "fixture broken pipe",
            "timeout": "timed out",
            "exit": "fixture failed",
            "exit-empty": "exited with status 3",
        }[failure]
        assert expected in error
        if failure in {"stdin", "write", "timeout"}:
            process.kill.assert_called_once_with()
            process.wait.assert_called_once()


def test_wsl_unavailable_platform_returns_without_process():
    c.sys.platform = "win32"
    assert "unavailable on this platform" in c.wsl_clipboard_copy("fixture")


def test_tmux_disabled_forwarding_never_starts_copy(monkeypatch):
    monkeypatch.setattr(c, "tmux_command_output", lambda args: "off")
    assert c.tmux_clipboard_copy("fixture") == "tmux clipboard forwarding is disabled"


@pytest.mark.parametrize(
    "returncode,stdout,stderr,expected",
    [
        (0, b"on\n", b"", "on\n"),
        (4, b"", b"fixture denied", "tmux failed: fixture denied"),
        (7, b"", b"", "tmux exited with status 7"),
    ],
)
def test_tmux_query_arguments_and_errors(
    monkeypatch, returncode, stdout, stderr, expected
):
    run = Mock(
        return_value=SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        )
    )
    monkeypatch.setattr(subprocess, "run", run)
    assert c.tmux_command_output(["show-options"]) == expected
    run.assert_called_once_with(
        ["tmux", "show-options"], capture_output=True, check=False
    )


def test_tmux_query_spawn_failure_keeps_reason(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=OSError("fixture missing tmux"))
    )
    assert "fixture missing tmux" in c.tmux_command_output(["info"])


@pytest.mark.parametrize("tty_outcome", ["ok", "open-error", "write-error"])
def test_osc_copy_prefers_tty_then_binary_stdout(monkeypatch, tty_outcome):
    tty = Mock()
    if tty_outcome == "write-error":
        tty.write.side_effect = OSError("fixture tty closed")
    opening = (
        Mock(side_effect=OSError("fixture no tty"))
        if tty_outcome == "open-error"
        else Mock(return_value=contextlib.nullcontext(tty))
    )
    monkeypatch.setattr(c, "open", opening)
    binary = io.BytesIO()
    stdout = io.TextIOWrapper(binary, encoding="utf-8")
    c.sys.stdout = stdout
    try:
        assert c.osc52_copy("Привет") is None
        opening.assert_called_once_with("/dev/tty", "wb")
        if tty_outcome == "ok":
            tty.write.assert_called_once()
            assert binary.getvalue() == b""
        else:
            assert binary.getvalue().startswith(b"\x1b]52;c;")
    finally:
        stdout.detach()


def test_windows_osc_uses_binary_stdout_without_dev_tty():
    c.sys.platform = "win32"
    binary = io.BytesIO()
    stdout = io.TextIOWrapper(binary, encoding="utf-8")
    c.sys.stdout = stdout
    try:
        assert c.osc52_copy("fixture") is None
        assert binary.getvalue().startswith(b"\x1b]52;c;")
    finally:
        stdout.detach()


def test_osc_too_large_does_not_open_terminal():
    assert "too large" in c.osc52_copy("x" * 100001)


@pytest.mark.parametrize(
    "ssh,wsl,tmux,native_ok,wsl_ok,linux_ok,terminal_ok,expected_order,label",
    [
        (True, False, False, False, False, False, True, ["osc"], "osc52"),
        (True, False, True, False, False, False, True, ["tmux"], "tmux"),
        (False, False, False, True, False, False, False, ["native"], "arboard"),
        (False, True, False, False, True, False, False, ["native", "wsl"], "wsl"),
        (
            False,
            True,
            False,
            False,
            False,
            False,
            True,
            ["native", "wsl", "osc"],
            "osc52",
        ),
        (False, False, False, False, False, True, False, ["native", "linux"], "xclip"),
        (
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            ["native", "linux", "osc"],
            "osc52",
        ),
        (
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            ["native", "linux", "osc"],
            None,
        ),
        (
            False,
            True,
            True,
            False,
            False,
            False,
            False,
            ["native", "wsl", "tmux", "osc"],
            None,
        ),
        (True, False, True, False, False, False, False, ["tmux", "osc"], None),
    ],
)
def test_public_routing_keeps_precedence_and_error_context(
    monkeypatch,
    ssh,
    wsl,
    tmux,
    native_ok,
    wsl_ok,
    linux_ok,
    terminal_ok,
    expected_order,
    label,
):
    calls = []
    lease = c.ClipboardLease.test()

    def simple(name, ok):
        def copy(text):
            assert text == "fixture"
            calls.append(name)
            return None if ok else name + " unavailable"

        return copy

    def native(text):
        calls.append("native")
        return (lease, None) if native_ok else (None, "native unavailable")

    def linux(text):
        calls.append("linux")
        return (lease, None, "xclip") if linux_ok else (None, "linux unavailable", None)

    for name, value in [
        ("is_ssh_session", ssh),
        ("is_wsl_session", wsl),
        ("is_tmux_session", tmux),
    ]:
        monkeypatch.setattr(c, name, lambda value=value: value)
    monkeypatch.setattr(c, "arboard_copy", native)
    monkeypatch.setattr(c, "linux_clipboard_copy", linux)
    monkeypatch.setattr(c, "wsl_clipboard_copy", simple("wsl", wsl_ok))
    monkeypatch.setattr(c, "tmux_clipboard_copy", simple("tmux", terminal_ok))
    monkeypatch.setattr(c, "osc52_copy", simple("osc", terminal_ok))
    actual_lease, error, actual_label = c.copy_to_clipboard_detailed("fixture")
    assert calls == expected_order and actual_label == label
    if label:
        assert error is None
        assert actual_lease is (lease if label in {"arboard", "xclip"} else None)
    else:
        assert actual_lease is None and "unavailable" in error
        if not ssh:
            assert "native" in error
        if wsl:
            assert "wsl" in error.lower()


def test_simple_public_wrapper_preserves_lease_and_error(monkeypatch):
    lease = c.ClipboardLease.test()
    monkeypatch.setattr(
        c, "copy_to_clipboard_detailed", lambda text: (lease, None, "fixture")
    )
    assert c.copy_to_clipboard("fixture") == (lease, None)


def test_command_availability_uses_executable_lookup(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/fixture/" + name if name == "available" else None,
    )
    assert c._command_available("available")
    assert not c._command_available("missing")
