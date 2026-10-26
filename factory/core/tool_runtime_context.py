"""Thread-local runtime context for tool execution.

Holds delegate_depth, iteration_budget, backend connection params,
interrupt flag, and progress callback so that delegate_task and
other tools can access runtime state without polluting kwargs.
"""
import threading

_tool_runtime_ctx = threading.local()


def set_runtime_context(
    *,
    delegate_depth: int = 0,
    iteration_budget=None,
    base_url: str = "",
    model: str = "",
    system_prompt: str = "",
    max_tokens: int = 4000,
    timeout_s: int = 300,
    parent_task_id: str = "",
    task_id: str = "",
    progress_callback=None,
    interrupt_event=None,
    interrupt_check=None,
):
    _tool_runtime_ctx.delegate_depth = delegate_depth
    _tool_runtime_ctx.iteration_budget = iteration_budget
    _tool_runtime_ctx.base_url = base_url
    _tool_runtime_ctx.model = model
    _tool_runtime_ctx.system_prompt = system_prompt
    _tool_runtime_ctx.max_tokens = max_tokens
    _tool_runtime_ctx.timeout_s = timeout_s
    _tool_runtime_ctx.parent_task_id = parent_task_id
    _tool_runtime_ctx.task_id = task_id
    _tool_runtime_ctx.progress_callback = progress_callback
    _tool_runtime_ctx.interrupt_event = interrupt_event
    _tool_runtime_ctx.interrupt_check = interrupt_check


def clear_runtime_context():
    for attr in (
        "delegate_depth",
        "iteration_budget",
        "base_url",
        "model",
        "system_prompt",
        "max_tokens",
        "timeout_s",
        "parent_task_id",
        "task_id",
        "progress_callback",
        "interrupt_event",
        "interrupt_check",
    ):
        try:
            delattr(_tool_runtime_ctx, attr)
        except AttributeError:
            pass


def get_runtime_context():
    return {
        "delegate_depth": getattr(_tool_runtime_ctx, "delegate_depth", 0),
        "iteration_budget": getattr(_tool_runtime_ctx, "iteration_budget", None),
        "base_url": getattr(_tool_runtime_ctx, "base_url", ""),
        "model": getattr(_tool_runtime_ctx, "model", ""),
        "system_prompt": getattr(_tool_runtime_ctx, "system_prompt", ""),
        "max_tokens": getattr(_tool_runtime_ctx, "max_tokens", 4000),
        "timeout_s": getattr(_tool_runtime_ctx, "timeout_s", 300),
        "parent_task_id": getattr(_tool_runtime_ctx, "parent_task_id", ""),
        "task_id": getattr(_tool_runtime_ctx, "task_id", ""),
        "progress_callback": getattr(_tool_runtime_ctx, "progress_callback", None),
        "interrupt_event": getattr(_tool_runtime_ctx, "interrupt_event", None),
    }


def request_interrupt():
    """Set the interrupt flag for the current thread's runtime context."""
    event = getattr(_tool_runtime_ctx, "interrupt_event", None)
    if event is not None:
        event.set()


def is_interrupt_requested() -> bool:
    """Check if interrupt was requested for current thread."""
    event = getattr(_tool_runtime_ctx, "interrupt_event", None)
    if event is not None and event.is_set():
        return True
    check = getattr(_tool_runtime_ctx, "interrupt_check", None)
    return bool(check()) if callable(check) else False


def notify_progress(event_type: str, tool_name: str = "", preview: str = ""):
    """Relay a child tool execution event to the parent progress callback."""
    cb = getattr(_tool_runtime_ctx, "progress_callback", None)
    if cb is not None:
        try:
            cb(event_type, tool_name=tool_name, preview=preview)
        except Exception:
            pass
