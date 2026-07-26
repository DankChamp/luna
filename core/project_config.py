from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _find_nearest(start: Path, filename: str, max_depth: int = 10) -> Path | None:
    current = start.resolve()
    for _ in range(max_depth):
        candidate = current / filename
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


@dataclass
class ProjectConfig:
    provider: str | None = None
    model: str | None = None
    rules: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    references: dict[str, str | dict] = field(default_factory=dict)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    permissions: dict | None = None
    agent_models: dict[str, str] = field(default_factory=dict)
    policies: list[dict] = field(default_factory=list)

    def get_command(self, name: str) -> str | None:
        return self.commands.get(name)

    def model_for_agent(self, agent_name: str) -> str | None:
        return self.agent_models.get(agent_name)


def discover(cwd: str | Path | None = None) -> ProjectConfig | None:
    start = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    cfg_path = _find_nearest(start, ".luna/config.json", max_depth=10)
    if cfg_path is None:
        return None
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return ProjectConfig(
        provider=raw.get("provider"),
        model=raw.get("model"),
        rules=raw.get("rules", []),
        commands=raw.get("commands", {}),
        references=raw.get("references", {}),
        mcp_servers=raw.get("mcp_servers", {}),
        permissions=raw.get("permissions"),
        agent_models=raw.get("agent_models", {}),
        policies=raw.get("policies", []),
    )


def discover_config_path(cwd: str | Path | None = None) -> Path | None:
    start = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    return _find_nearest(start, ".luna/config.json", max_depth=10)
