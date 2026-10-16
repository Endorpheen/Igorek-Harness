# Codex TUI source comparison — 16 October 2026

This review separates source-backed changes implemented in Igorek from remaining
upstream capabilities. The reference application itself was not natively tested.

## Pinned reference

The official latest stable release at review time is
[Codex 0.163.0](https://github.com/openai/codex/releases/tag/rust-v0.163.0),
published on 15 October 2026. Reviewed source commit:
`83086188ae0614bd882818ff1aa23f3194962012`.
A fresh dedicated reference checkout was obtained; older references were retained.
The previously available `codex-openai-latest` checkout was at
`b5edeb98a0f3a7a0f43243f6ae06bc7de6cfacc0` (5 April 2026). That dates the reference,
not every individual Python port in Igorek.

Igorek's existing terminal interface already includes SGR mouse capture, drag
selection, wheel navigation, composer selection, expandable tool cells, clipboard
fallbacks for local/WSL/tmux/OSC52 environments, prompt forms and paste-burst logic.
A new mouse subsystem is not needed simply because upstream added more features.

## Findings and useful transfers

| Area | Codex 0.163.0 source behavior | Igorek after this change | Adoption status |
| --- | --- | --- | --- |
| Clipboard responsiveness | `clipboard_copy/worker.rs` coordinates copy/paste requests and pending results separately from the event loop | Mouse delivery uses `ClipboardService.submit`, with one off-loop job and explicit busy/confirmed results; synchronous API retained | Implemented and tested: slow backends cannot block UI; late completion cannot clear a newer selection |
| Selection retention | `transcript_view/right_click_copy_tests.rs` proves failed/unconfirmed copies retain the selection and reading position; only confirmed copy clears it | Failed/unconfirmed copy retains selection; confirmed local copy clears the same selection; right click retries | Implemented and tested, including unconfirmed OSC52 |
| Logical transcript and reflow | `transcript_view/selection.rs`, `text_logical.rs`, `layout.rs` anchor selection to transcript content rather than only screen rows | `mouse_frame.py` and `mouse_controller.py` extract selected rendered/full lines with screen-to-absolute row coordinates | Medium priority: stable logical anchors across resize, wrapped text, streaming append and expanded tool cells |
| Mouse granularity and speed | `selection.rs` uses `SelectionUnit::from_clicks`; `input.rs` accumulates fractional configured mouse-scroll movement | Current mouse handlers implement press/drag/release and a fixed wheel path, without equivalent click-count units/fractional accumulation | Medium priority: word/line selection and configurable scroll speed, tested with actual SGR input |
| Keyboard transcript navigation | `transcript_view/select_mode.rs`, `select_mode/motion.rs` and `search.rs` implement keyboard selection and transcript search; bookmark tests preserve navigation positions | Current fullscreen/viewer paths support scrolling and selection but lack this unified transcript search/select-mode layer | High value for phone terminals: search, next/previous result and keyboard copy mode before adding cosmetic panels |
| Paste policy | `app/right_click_paste_tests.rs` explicitly distinguishes desktop, SSH, VS Code, WSL, inline mode and Android | Existing clipboard and mouse modules already distinguish terminal/local backends, but do not implement this complete paste-policy surface | Selective transfer only: never promise right-click native paste in Android; retain terminal ownership where appropriate |
| External editor | Upstream retains a dedicated editor handoff and composer state; tests cover it | `tui/external_editor.py` exists, but no Ctrl+G/run-editor wiring was found in the active composer/session paths reviewed | Verify and wire the existing helper with draft restoration and real terminal suspend/resume acceptance rather than copying another helper |

## Exact upstream reading locations

All links are pinned to the reviewed stable commit:

- [Clipboard worker](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/clipboard_copy/worker.rs)
- [Mouse input and fractional wheel movement](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/transcript_view/input.rs)
- [Selection units and lifecycle](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/transcript_view/selection.rs)
- [Right-click copy acceptance](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/transcript_view/right_click_copy_tests.rs)
- [Transcript search](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/transcript_view/search.rs)
- [Keyboard selection motions](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/transcript_view/select_mode/motion.rs)
- [Paste environment policy](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/app/right_click_paste_tests.rs)
- [Editor handoff acceptance](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/tests/suite/external_editor_tests.rs)

## Adoption order and boundaries

The launcher, shutdown ownership, persistent inline mode, compact header and
asynchronous copy/retained selection are implemented. Transcript search/keyboard
selection, logical resize anchors, click units, fractional scroll and editor
handoff remain candidates, not features of this release.

Port user-visible contracts into the existing Rich/prompt_toolkit architecture.
Do not replace the backend, gateway, desktop routes, authorization policy or
worker lifecycle with Rust TUI implementation details. Existing weak-model guards,
cloud-model behavior and approvals are unrelated and must remain intact.

Each adopted interaction needs positive and compatibility cases plus actual PTY
geometry, raw keys/SGR events and cursor-query responses. Source/snapshot tests do
not prove mouse behavior on a physical Termux phone. Physical phone acceptance remains outstanding. Implemented features have local
terminal and synthetic-provider acceptance, not real account consent.

## Lifecycle and inline-mode transfers

`cli.rs` defines `--no-alt-screen`; `transcript_mode.rs` separates owned and
terminal transcripts. Igorek uses a persistent prompt_toolkit application with
fullscreen enabled or disabled. The old inline path recreated prompts and could
lose fast input after profile switches; PTY acceptance exposed that failure.

`tui/src/lib.rs::start_app_server` distinguishes embedded ownership, an existing
local daemon and a remote server. `app-server-client/src/remote.rs` disconnects
a client without shutting down the server. `cli/src/main.rs` exposes
`app-server daemon start|restart|stop|version`. Igorek adopts these names and
explicit shared-server ownership, retaining Python SQLite tasks, profile routing
and the existing scheduler. Default ordinary Ctrl+C exits only its client.

The reference also includes updater behavior and bounded forced shutdown. These
were not copied: auto-updates were cancelled by the user; Igorek refuses busy
daemon stops rather than force-dropping work. Owner-only sockets and per-database
scheduler leases protect shared operation. The daemon is opt-in, not a boot service.

- [Transcript mode](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/transcript_mode.rs)
- [TUI server selection](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/tui/src/lib.rs)
- [Remote shutdown](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/app-server-client/src/remote.rs)
- [Daemon lifecycle](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/app-server-daemon/README.md)
