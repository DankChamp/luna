# Luna

<div align="center">

[![CI](https://github.com/DankChamp/luna/actions/workflows/ci.yml/badge.svg)](https://github.com/DankChamp/luna/actions/workflows/ci.yml)
[![CodeQL](https://github.com/DankChamp/luna/actions/workflows/codeql.yml/badge.svg)](https://github.com/DankChamp/luna/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-00FF9C.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Rich](https://img.shields.io/badge/Terminal-Rich-FF6B00?logo=textual&logoColor=white)](https://github.com/Textualize/rich)
[![Void Linux](https://img.shields.io/badge/Void_Linux-runit%20%2B%20XBPS-1c1c1c?logo=linux&logoColor=white)](https://voidlinux.org)

</div>

**Your coder — an opencode-style CLI coding assistant. Highly customizable, local-first, with tool calling and agent orchestration.**

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/DankChamp/luna
cd luna
./setup.sh      # one-time: venv, deps, config

# Run
./luna          # interactive TUI
./luna "your coding task here"  # direct prompt
```

### Requirements
- Python 3.10+
- Void Linux (or any Linux with Ollama/local LLM endpoint)
- Ollama running locally (or any OpenAI-compatible API)

---

## Architecture

Luna is built as a modular, extensible coding agent with these core components:

```
luna/
├── cli.py                 # Entry point, argument parsing
├── luna.py                # Main agent loop & orchestration
├── config.py              # Configuration (Pydantic Settings)
├── core/
│   ├── agent/             # Agent logic, tool calling, planning
│   ├── llm/               # LLM providers (Ollama, OpenAI, Anthropic)
│   ├── memory/            # Conversation history, context management
│   ├── session/           # Session persistence, restore
│   └── tools/             # Built-in tools (file ops, shell, search, etc.)
├── tools/                 # Additional tool implementations
├── ui/                    # Rich-based TUI components
├── bridge/                # External integrations (opencode, etc.)
└── tests/                 # Pytest suite
```

### Key Features

| Feature | Description |
|---------|-------------|
| **Multi-provider LLM** | Ollama, OpenAI, Anthropic — unified interface |
| **Tool calling** | File read/write, shell exec, search, grep, LSP integration |
| **Session persistence** | SQLite-backed, resume where you left off |
| **Rich TUI** | Interactive terminal interface with syntax highlighting |
| **Extensible tools** | Plugin-style tool system, add custom tools easily |
| **Local-first** | Runs entirely on your machine, no cloud required |
| **OpenTelemetry** | Built-in tracing for debugging agent behavior |

---

## Configuration

Luna uses Pydantic Settings — all config via `.env` or environment variables:

```bash
# LLM Provider
LUNA_LLM_PROVIDER=ollama          # ollama | openai | anthropic
LUNA_OLLAMA_BASE_URL=http://localhost:11434
LUNA_OLLAMA_MODEL=llama3.1:8b

# OpenAI-compatible (LM Studio, llama.cpp, vLLM, etc.)
LUNA_OPENAI_BASE_URL=http://localhost:1234/v1
LUNA_OPENAI_API_KEY=              # optional
LUNA_OPENAI_MODEL=your-model

# Anthropic
LUNA_ANTHROPIC_API_KEY=sk-ant-...

# Agent behavior
LUNA_MAX_ITERATIONS=10
LUNA_TEMPERATURE=0.2
LUNA_SYSTEM_PROMPT_PATH=.luna/system_prompt.md
```

See `.env.example` for all options.

---

## Usage

### Interactive TUI (recommended)
```bash
./luna
```
- `Tab` — toggle sidebar (tools, history, config)
- `Ctrl+C` — cancel current task
- `Ctrl+D` — exit

### Direct Prompt
```bash
./luna "refactor the auth module to use async SQLAlchemy"
./luna "add tests for the session manager" --max-iterations 5
```

### With Context Files
```bash
./luna --context core/agent.py --context core/tools/ "explain this code"
```

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Lint
ruff check .

# Type check
mypy .

# Test
pytest -q --tb=short

# Run from source
python -m luna
```

### Project Structure for Contributors

| Directory | Purpose |
|-----------|---------|
| `core/agent/` | Agent loop, planning, tool orchestration |
| `core/llm/` | Provider implementations, streaming, tool calling |
| `core/tools/` | Built-in tools (file, shell, search, LSP) |
| `core/memory/` | Conversation history, summarization, context window |
| `tools/` | Additional tool implementations |
| `ui/` | Rich TUI components, layouts, themes |
| `bridge/` | External tool integrations |

### Adding a New Tool

1. Create `core/tools/my_tool.py` inheriting from `BaseTool`
2. Implement `name`, `description`, `parameters`, `execute()`
3. Register in `core/tools/__init__.py`
4. Add tests in `tests/test_my_tool.py`

---

## Roadmap

- [ ] LSP integration (go-to-def, hover, diagnostics)
- [ ] Multi-agent orchestration (planner + executor)
- [ ] Voice input/output (Vosk + Piper)
- [ ] Web UI (optional, for remote access)
- [ ] Plugin marketplace / tool registry

---

## Contributing

PRs welcome! Please:

1. Fork the repo & create a feature branch
2. Run `ruff check . && mypy . && pytest -q` locally
3. Follow the existing code style (ruff config in `pyproject.toml`)
4. Add tests for new functionality
5. Open a PR with a clear description

```bash
# Quick validation
ruff check . && mypy . && pytest -q
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.