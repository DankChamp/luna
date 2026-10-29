# ADR 0001: Record Architecture Decisions

## Status
Accepted

## Context
We need to document the architectural decisions made during Luna's development so that future contributors can understand the rationale behind key design choices.

## Decision
We will use Architecture Decision Records (ADRs) as described by Michael Nygard. Each ADR will be a markdown file in `docs/architecture/` with a sequential number prefix.

## Consequences
- All significant architectural decisions will be documented
- New contributors can understand the "why" behind design choices
- Decisions can be revisited with full context

---

# ADR 0002: Single Entry Point with Subcommands

## Status
Accepted

## Context
Luna originally had two entry points: `luna.py` (interactive TUI) and `cli.py` (command-line commands). This led to code duplication and confusion.

## Decision
Consolidate into a single `luna.py` entry point using argparse subcommands:
- `luna` — interactive TUI (default)
- `luna run "prompt"` — non-interactive execution
- `luna serve` — bridge server
- `luna agent|session|config|model|plugin` — management commands

## Consequences
- Eliminates ~500 lines of duplicated initialization code
- Single source of truth for component initialization
- Consistent CLI UX across all commands

---

# ADR 0003: Provider Configuration via Environment Variables

## Status
Accepted

## Context
Luna originally used hardcoded provider configs (NVIDIA NIM + local Ollama) in `config.py`. Users wanted OpenCode-style provider switching with API keys.

## Decision
Adopt a unified provider configuration schema:
- Single `provider` field to select active provider: `nvidia|local|openai|anthropic`
- Nested config per provider: `nvidia.api_key`, `local.base_url`, etc.
- Environment variable prefix: `LUNA_PROVIDER__<PROVIDER>__<FIELD>`
- Provider configs loaded from `Settings` (Pydantic Settings)
- `ConfigManager` only handles agent/UI config (YAML)

## Consequences
- OpenCode-style UX for provider switching
- Easy to add new providers (just add to Settings)
- Clear separation: provider config = env, agent config = YAML

---

# ADR 0004: Decomposed Core Architecture

## Status
Accepted

## Context
Luna's original `luna.py` was a 1000+ line monolith handling everything: provider management, tool execution, session persistence, UI, command dispatch.

## Decision
Decompose into focused modules with clear interfaces:
- `AgentCore` — message loop, tool execution, history management
- `ToolExecutor` — permission checks, snapshots, LSP hooks, result formatting
- `SessionController` — session lifecycle, debounced saves, compaction
- `CommandDispatcher` — slash commands, @mentions, custom commands
- `LunaApp` — TUI layout, keybindings, rendering
- `AIRouter` / `ProviderManager` — provider abstraction, model switching
- `ConfigManager` — agent/UI config (YAML + JSON migration)
- `SkillEngine` / `SubagentEngine` — skill/subagent loading and execution

## Consequences
- Each module is independently testable
- Clear separation of concerns
- Easier to swap implementations (e.g., different session backends)
- Protocol types enable loose coupling

---

# ADR 0005: Session Persistence with SQLite + SQLAlchemy

## Status
Accepted

## Context
Luna originally used JSON files for session storage. This didn't scale well and made querying difficult.

## Decision
Use SQLite with SQLAlchemy/aiosqlite for session storage:
- `sessions` table with project metadata (path, repo, branch, commit)
- `messages` table with role, content, tool_calls_json
- `tool_calls` table for detailed tool execution history
- Auto-migration from legacy JSON sessions on first run
- Token-aware compaction preserving tool call/result pairs

## Consequences
- Efficient querying and listing of sessions
- ACID guarantees for session data
- Rich metadata for session previews
- Extensible schema for future features

---

# ADR 0006: Ultralight Optimizations

## Status
Accepted

## Context
Luna's default install was ~80MB with 3s cold start. Goal: <15MB install, <500ms cold start (minimal mode).

## Decision
Implement progressive loading and optional dependencies:
- Core deps only: `httpx, pydantic, rich, prompt-toolkit, tiktoken, pyperclip, structlog` (~8 packages)
- Optional dependency groups: `nvidia`, `local`, `openai`, `anthropic`, `bridge`, `sessions`, `observability`, `yaml`, `anyio`
- Lazy imports for heavy deps (anthropic, openai, sqlalchemy, aiohttp, opentelemetry)
- Minimal UI mode (`--minimal`): plain `input()` loop, no prompt_toolkit/Rich
- Session DB lazy initialization
- Inline small modules (`paths`, `modes`, `errors`) into `core/__init__.py`
- Nuitka single-binary build for distribution

## Consequences
- `pip install -e .` = minimal install
- `pip install -e .[openai,anthropic]` = cloud providers
- `pip install -e .[full]` = everything
- Minimal mode works without TTY for CI/testing

---

# ADR 0006: Plugin System with Entry Points

## Status
Accepted

## Context
Users want to extend Luna with custom tools without modifying core code.

## Decision
Implement plugin system with two discovery mechanisms:
1. **Directory-based**: `~/.luna/plugins/<name>/plugin.py` with `Plugin` subclass
2. **Entry point-based**: `pip install` packages declaring `luna.plugins` entry point

Plugin interface:
```python
class Plugin:
    def initialize(self, registry: ToolRegistry) -> None: ...
    def shutdown(self) -> None: ...

class ToolPlugin(Plugin):
    def get_tools(self) -> list[ToolDef]: ...
```

Plugins can provide tools, modify config, or add custom commands.

## Consequences
- Users can share plugins as PyPI packages
- No core modifications needed for custom tools
- Clear extension point for community contributions

---

# ADR 0007: Structured Logging with structlog

## Status
Accepted

## Context
Debugging agent behavior was difficult with ad-hoc print statements.

## Decision
Use `structlog` for structured logging:
- JSON output for production / log aggregation
- Console output for development
- Contextual loggers with bound fields (session_id, provider, model, etc.)
- Integration with OpenTelemetry for tracing

## Consequences
- Consistent log format across all components
- Easy filtering and querying in log aggregation systems
- Rich context for debugging agent loops

---

# ADR 0008: Protocol Types for Loose Coupling

## Status
Accepted

## Context
Direct imports between core modules created tight coupling and circular import risks.

## Decision
Define Protocol types in `core/protocols.py` for all major interfaces:
- `ToolRegistryProtocol`, `AgentCoreProtocol`, `AIProviderProtocol`
- `SessionManagerProtocol`, `ConfigManagerProtocol`, `RouterProtocol`
- `SkillManagerProtocol`, `SubagentManagerProtocol`, `ReferenceManagerProtocol`
- `ToolExecutorProtocol`

Components depend on protocols, not concrete implementations. Concrete classes are wired at startup in `luna.py`.

## Consequences
- Loose coupling between modules
- Easy to mock in tests
- Clear contract documentation
- Supports `mypy --strict` with proper typing