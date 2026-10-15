# Terminal interface architecture

Rich/prompt_toolkit is the only CLI frontend. Its current rendering and mouse
modules are retained; the Textual/plain frontends and their unused helpers are
retired. Display modes inside the current frontend are not separate clients.

## Responsibilities

| Module | Responsibility |
| --- | --- |
| `cli_chat_rich.py` | Compose the current chat interface and session entry point |
| `chat_session.py` | Input/task lifecycle and full-screen application ownership |
| `cli_chat_idle.py` | Idle prompt, help and transcript pager |
| `cli_chat_active.py` | Active task display |
| `cli_chat_approval.py` | Approval interaction |
| `cli_chat_rich_shared.py` | Shared terminal helpers |
| `terminal_render_log.py` | Terminal rendering diagnostics |
| `tui/chatwidget.py`, `tui/cells/` | Chat rows and their presentation |
| `tui/bottom_pane/` | Input, footer and completion behavior |
| `tui/mouse_controller.py` | Mouse routing and wheel behavior |

These paths are relative to `factory/core/`. `cli_chat.py` also provides shared
history, prompt and session helpers consumed outside the terminal frontend.
Removing that module would break more than the retired plain loop.

## Request and rendering flow

The input layer submits a user turn; the executor produces typed model, tool,
approval and completion events. The reducer updates chat items. Rendering reads
those items and the current terminal geometry. Reasoning and approval blocks
have separate lifecycles; collapsing reasoning must not discard the answer.

Task completion and application exit are different events. The full-screen
owner exits an active application only once. A second exit after prompt_toolkit
has completed raises an error instead of closing cleanly.

Do not reconstruct a terminal display from phrases in model text. Follow the
recorded event types, tool IDs, task state and content boundaries.

## Verification

Use a child PTY with a real size, answer cursor-position queries and send raw
keyboard/SGR mouse input. Check scrolling, hover, clicks, overlays, resize,
Unicode, code blocks, lists, empty values, reasoning and approvals. DummyOutput
does not establish real mouse coordinates or terminal rendering.

When a fix appears absent, check the installed tool and running process before
changing layout again. Source, installed wheel, runtime and resumed transcript
are separate layers. See the [debugging playbook](../development/DEBUGGING_PLAYBOOK.md).
