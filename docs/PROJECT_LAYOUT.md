# Project layout

Igorek has one shared Python agent package and several user interfaces. The
layout separates runtime code, interfaces, tests, assets, packaging and docs.

| Directory | Responsibility |
| --- | --- |
| `factory/core/` | Agent execution, worker queues, tools, sessions and policies |
| `factory/core/vault/` | Credential storage, owner lifecycle and broker permissions |
| `factory/core/isolation/` | VM lifecycle, host/guest protocol, egress and workspace boundaries |
| `factory/core/mobile_bridge/` | Authenticated mobile/session API |
| `factory/core/realtime_voice/` | Voice sessions and transport |
| `factory/core/mcp_tasks/` | Profile-scoped MCP and private integrations |
| `factory/core/acp_bridge/` | Editor integration through ACP |
| `factory/core/tui/` | Current terminal rendering, input and mouse components |
| `factory/web_ui/` | HTTPS server and the shared desktop/Web UI chat renderer |
| `factory/api_server_core/` | Separate inference API and workflow implementation |
| `factory/config/` | Bundled defaults and provider profiles |
| `factory/mcp_servers/` | MCP server adapters |
| `factory/scripts/`, `factory/deploy/` | Operations, installation and service templates |
| `desktop/` | Electron main/preload/controllers and its managed Python backend |
| `desktop/tests/` | Desktop Node contract and interaction tests |
| `desktop/packaging/` | Runtime builders, installers and release verification |
| `desktop/assets/` | Desktop images, notices and release notes |
| `android/` | Kotlin/Compose client, JVM tests and device tests |
| `tests/`, `factory/tests/` | Python regressions and terminal characterization tests |
| `tools/` | Repository checks, isolated test runner and clean-source builds |
| `docs/` | Architecture, development, integrations and operations documentation |

## Entry points

`pyproject.toml` registers `igorek-factory`, `factory-acp` and `igorek-sudo`.
`desktop/main.cjs` owns Electron windows, IPC and controller lifecycles.
`desktop/backend.py` starts the managed bridge and Web UI; it does not use the
retired web monitor. Android connects to that same session service.

The task API in `factory/factory_api.py` and inference server in
`transformers_api_server.py` have different contracts. Similarly, the root
`prompt_assembly.py` and `factory/core/prompt_assembly.py` serve distinct callers;
matching basenames do not establish duplication or dead code.

The single CLI frontend is Rich/prompt_toolkit. Its shared history and prompt
helpers also serve other interfaces. The current `core/tui/` modules remain live.
The unused prototype `core/tui/app.py` coordinator has been retired; the active
CLI builds its application in `cli_chat_active.py`. Shared widgets, event types,
frame rendering and mouse handling retain their existing consumers.
Skills are external instruction packages; see [skills](development/SKILLS.md).
The old `factory/Skills/` catalog advertised missing files and has been removed.

## Packaging boundaries

`desktop/packaging/bundle_sources.py` declares the shared application and backend
file inventories. Builders and installers live together, while the installed
snapshot retains its established filenames. Update that inventory when moving
or adding a runtime module. `build-runtime.py` uses the same backend list, so a
source build cannot silently omit a module that packaging would later overlay.

Python wheels are built by `tools/build_wheel.py` from a clean source inventory,
with source hashes checked after building. Existing `build/lib` copies are not
build inputs. Development environments, local data and build outputs are not
part of the public source layout.

## Changing the layout

Move cohesive groups with verified consumers. Inspect imports, subprocess
entry points, dynamic loaders, static URLs and package inventories before moving
a file. Preserve saved-data formats and public commands unless a deliberate
retirement is documented. A large file or an old filename alone is not dead code.

The desktop test and packaging groups are now separate. Remaining controllers
and Python modules retain their import paths. Larger coordinator splits require
behavioral boundaries and regression evidence, rather than directory renaming
alone. Keep new changes scoped to the domain they implement.
