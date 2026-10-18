## Purpose
Multi-platform agentic harness and Electron desktop app providing secure workspace sandboxing, LLM tool execution loops, and autonomous background coding tasks. Used by developers for local and remote AI-assisted development.

## Key Technologies
Python, Node.js, Electron, C++, Microsoft Detours, Bubblewrap, Git

## Top-Level Structure
* `factory/`: Core Python agentic runtime and execution
* `desktop/`: Electron application shell and IPC handlers
* `desktop/native/`: C++ Windows pipe adapter and hooks
* `tests/`: Integration and platform support verification suites
* `docs/`: Release logs and operational specifications
* `pyproject.toml`: Python project configuration and entrypoints

## Key Concepts
* OpenCode Assignment: Isolated background programming task session
* WindowsSandbox: Win32 restricted token and job object isolation
* Vault: Encrypted local storage for user credentials
* Managed Mode: Desktop mode using owned runtime and strict partitions
* OpenAI Responses: SSE wire adapter for model chat completions
* Detours Pipe Adapter: C++ DLL supplying DACLs to restricted child pipes
* Transit Grants: Temporary ancestor directory permissions for traversal
* Compaction: Reactive dialogue summarization preventing context limits
* Steer: Real-time message injection during active task execution
* AgentRuntime: Shared execution mutex and worker management system
* Synthetic LLM Provider: Local HTTP SSE server for protocol testing
