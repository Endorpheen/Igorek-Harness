"""Session-scoped access to terminals already owned by a terminal manager."""
from ..isolation.terminal_manager import TerminalError


def terminal_process(manager, session_id: str, prefix: str) -> list[dict]:
    state = manager.terminal_for_session(session_id)
    if state is None:
        return []
    try:
        if manager.poll_exit(state.terminal_id) is not None:
            return []
    except TerminalError:
        return []
    return [{"id": prefix + state.terminal_id, "kind": "terminal", "label": "Фоновый терминал",
             "started_at": state.opened_at, "stopping": False}]


def stop_terminal(manager, session_id: str, identity: str, prefix: str) -> bool:
    if not identity.startswith(prefix):
        return False
    state = manager.terminal_for_session(session_id)
    return bool(state and prefix + state.terminal_id == identity and manager.close_terminal(state.terminal_id))
