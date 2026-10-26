"""Tests for maybe_persist_tool_result output truncation on persistence failure."""

from factory.core import tool_result_store


def _large_bash_result() -> str:
    """Build a bash result that exceeds the default persistence threshold."""
    return "\n".join(f"bash output line {index}" for index in range(600)) + "\n"


def test_maybe_persist_tool_result_truncates_bash_output_on_save_failure(tmp_path, monkeypatch):
    """Truncate large bash output with head and tail when atomic write fails with OSError."""
    content = _large_bash_result()
    config = tool_result_store.DEFAULT_TOOL_RESULT_STORE_CONFIG
    assert len(content) > config.threshold_for_tool("Bash")

    def _raise_oserror(*args, **kwargs):
        raise OSError("simulated atomic write failure")

    monkeypatch.setattr(tool_result_store, "atomic_write_file", _raise_oserror)

    result = tool_result_store.maybe_persist_tool_result(
        content=content,
        tool_name="Bash",
        tool_use_id="toolu_failure19",
        workdir=str(tmp_path),
    )

    assert result.startswith("bash output line 0\n")
    assert result.endswith("bash output line 599\n")
    assert len(result) < len(content)
    assert "the full result could not be saved for Read" in result
    assert "Do not assume the producing action failed" in result
    assert "Full output saved to:" not in result
