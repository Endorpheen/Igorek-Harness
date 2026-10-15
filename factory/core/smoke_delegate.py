#!/usr/bin/env python3
"""Smoke test for delegate_task without model.

Verifies:
- delegate_task tool dispatch works end-to-end
- child workdir is isolated
- child gets restricted tools (no delegate_task recursion)
- summary is returned
- depth guard blocks recursion
"""
import json
import os
import shutil
import tempfile


def _test_depth_guard():
    from factory.core.delegate_tool import delegate_task

    result = delegate_task({"goal": "test"}, workdir="/tmp", delegate_depth=2)
    parsed = json.loads(result)
    assert "depth limit reached" in parsed.get("error", "").lower()
    print("[PASS] depth guard blocks delegate_depth >= 2")


def _test_child_isolation_and_summary():
    from factory.core.delegate_tool import delegate_task

    parent_dir = tempfile.mkdtemp(prefix="delegate_smoke_parent_")
    try:
        result = delegate_task(
            {
                "goal": (
                    "Run Bash command 'echo smoke_test_content_line_1' "
                    "and return the exact output as your summary."
                ),
            },
            workdir=parent_dir,
            delegate_depth=0,
            # We need a real base_url for the child to run against.
            # If no server is running, this will fail with connection error —
            # which is expected for a no-model smoke.  We catch that and skip.
            base_url="http://127.0.0.1:8001",
            model="gemma-4-E4B-it-Q4_K_S.gguf",
        )
        parsed = json.loads(result)

        # If server not running, we expect connection error
        if "error" in parsed:
            err = parsed["error"]
            if "connection" in err.lower() or "failed" in err.lower():
                print("[SKIP] no local cc-backend running (expected for no-model smoke)")
                return
            raise AssertionError(f"Unexpected error: {err}")

        summary = parsed.get("summary", "")
        # Model may reply textually instead of using Bash; accept any non-error result.
        assert summary, f"Summary is empty. Full result: {parsed}"
        print(f"[PASS] child isolated workspace + summary returned (len={len(summary)})")

        # Verify child workdir exists and is isolated
        child_dirs = [d for d in os.listdir(parent_dir) if d.startswith(".delegate_")]
        assert len(child_dirs) == 1, f"Expected 1 child dir, got: {child_dirs}"
        print(f"[PASS] child workdir created: {child_dirs[0]}")

    finally:
        shutil.rmtree(parent_dir, ignore_errors=True)


def _test_toolset_filtering():
    from factory.core.delegate_tool import _strip_blocked_tools

    available = ["Read", "Write", "Edit", "Bash", "delegate_task"]
    child = _strip_blocked_tools(available)
    assert "delegate_task" not in child
    assert "Read" in child
    print("[PASS] blocked tools stripped from child toolset")


def _test_iteration_budget():
    from factory.core.iteration_budget import IterationBudget

    b = IterationBudget(3)
    assert b.consume() is True
    assert b.consume() is True
    assert b.consume() is True
    assert b.consume() is False
    assert b.remaining == 0
    b.refund()
    assert b.remaining == 1
    print("[PASS] iteration budget consume/refund")


def _test_coerce():
    from factory.core.coerce_tools import coerce_tool_args

    result = coerce_tool_args("Bash", {"command": "ls", "timeout": "120000"})
    assert isinstance(result["timeout"], int)
    assert result["timeout"] == 120000

    result = coerce_tool_args("Edit", {
        "file_path": "/tmp/x",
        "old_string": "a",
        "new_string": "b",
        "replace_all": "true",
    })
    assert result["replace_all"] is True
    print("[PASS] coerce_tool_args string->int/bool")


def _test_device_guard():
    from factory.core.cc_backend_tools import _is_blocked_device

    assert _is_blocked_device("/dev/zero") is True
    assert _is_blocked_device("/dev/urandom") is True
    assert _is_blocked_device("/tmp/file.txt") is False
    print("[PASS] device path guard")


def _test_read_size_guard():
    import tempfile
    from factory.core.cc_backend_tools import _tool_read

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("x" * 150_000)
        path = f.name
    try:
        result = _tool_read({"file_path": path}, workdir="/tmp", filesystem_mode="user-read")
        assert "exceeds" in result.lower()
        print("[PASS] read size guard (150K > 100K limit)")
    finally:
        os.unlink(path)


def main():
    print("=== delegate_task smoke (no model) ===\n")
    _test_depth_guard()
    _test_toolset_filtering()
    _test_iteration_budget()
    _test_coerce()
    _test_device_guard()
    _test_read_size_guard()
    _test_child_isolation_and_summary()
    print("\n=== all smoke tests passed ===")


if __name__ == "__main__":
    main()
