from __future__ import annotations

import contextlib
import json
import os
import stat
from dataclasses import dataclass, field
from typing import Any

import yaml

from core import config_home
from core.errors import ConfigError, ConfigMigrationError


@dataclass
class AgentConfig:
    model: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    prompt: str = ""
    variant: str = ""
    temperature: float | None = None
    top_p: float | None = None
    color: str = ""
    hidden: bool = False
    native: bool = True
    mode: str = "primary"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class LunaConfig:
    """Full Luna configuration (agents + UI settings)."""
    active_agent: str = "build"
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    default_model: str | None = None
    theme: str = "neon"
    locale: str = "en"


class ConfigManager:
    """Manages Luna configuration (agents, UI) with YAML support and JSON auto-migration.

    Provider configuration comes from Settings (environment variables).
    """

    def __init__(self, env_settings):
        self.env = env_settings
        self.config_path = config_home() / "config.yaml"
        self.json_config_path = config_home() / "config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config: LunaConfig = self._load()

    def _load(self) -> LunaConfig:
        """Load config from YAML, with JSON auto-migration fallback."""
        if self.config_path.exists():
            try:
                with self.config_path.open() as f:
                    data = yaml.safe_load(f) or {}
                return self._dict_to_config(data)
            except Exception as e:
                raise ConfigError(f"Failed to load YAML config: {e}", str(self.config_path))

        # Try JSON migration
        if self.json_config_path.exists():
            return self._migrate_from_json()

        return self._seed_defaults()

    def _migrate_from_json(self) -> LunaConfig:
        """Migrate from old JSON config to YAML."""
        try:
            with self.json_config_path.open() as f:
                json_data = json.load(f)
        except Exception as e:
            raise ConfigMigrationError(
                f"Failed to read JSON config: {e}",
                str(self.json_config_path),
                str(self.config_path),
            )

        config = LunaConfig()

        # Migrate active agent
        config.active_agent = json_data.get("active", "build")

        # Migrate agents (basic structure)
        if "agents" in json_data:
            for name, a in json_data["agents"].items():
                config.agents[name] = AgentConfig(
                    model=a.get("model"),
                    permissions=a.get("permissions", {}),
                    description=a.get("description", ""),
                    prompt=a.get("prompt", ""),
                    variant=a.get("variant", ""),
                    temperature=a.get("temperature"),
                    top_p=a.get("top_p"),
                    color=a.get("color", ""),
                    hidden=a.get("hidden", False),
                    native=a.get("native", True),
                    mode=a.get("mode", "primary"),
                    options=a.get("options", {}),
                )

        # Ensure default agents exist
        self._ensure_default_agents(config)

        # Write migrated config
        self.config_path.write_text(yaml.dump(self._config_to_dict(config), default_flow_style=False))
        with contextlib.suppress(OSError):
            os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)

        # Backup old JSON
        backup_path = self.json_config_path.with_suffix(".json.backup")
        self.json_config_path.rename(backup_path)

        return config

    def _seed_defaults(self) -> LunaConfig:
        """Create default config with built-in agents."""
        config = LunaConfig(active_agent="build")
        self._ensure_default_agents(config)
        self.save()
        return config

    def _ensure_default_agents(self, config: LunaConfig) -> None:
        """Ensure default agents (build, plan) exist."""
        if "build" not in config.agents:
            config.agents["build"] = AgentConfig(
                model=None,
                permissions={
                    "edit": "allow",
                    "bash": "allow",
                    "read": "allow",
                    "write": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "task": "allow",
                    "question": "allow",
                },
                description="Default agent for coding tasks",
                native=True,
                mode="primary",
            )

        if "plan" not in config.agents:
            config.agents["plan"] = AgentConfig(
                model=None,
                permissions={
                    "edit": "deny",
                    "bash": "allow",
                    "read": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "question": "allow",
                    "write": "deny",
                },
                description="Read-only planning agent",
                native=True,
                mode="primary",
            )

        if "general" not in config.agents:
            config.agents["general"] = AgentConfig(
                model=None,
                permissions={
                    "edit": "allow",
                    "bash": "allow",
                    "read": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "question": "allow",
                    "todowrite": "deny",
                },
                description="General-purpose subagent for parallel tasks",
                native=True,
                mode="subagent",
            )

        if "explore" not in config.agents:
            config.agents["explore"] = AgentConfig(
                model=None,
                permissions={
                    "read": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "bash": "allow",
                    "webfetch": "allow",
                    "websearch": "allow",
                    "edit": "deny",
                    "write": "deny",
                },
                description="Codebase exploration agent",
                native=True,
                mode="subagent",
            )

    def _config_to_dict(self, config: LunaConfig) -> dict:
        """Convert config to dictionary for YAML serialization."""
        return {
            "active_agent": config.active_agent,
            "default_model": config.default_model,
            "theme": config.theme,
            "locale": config.locale,
            "agents": {
                name: {
                    "model": a.model,
                    "permissions": a.permissions,
                    "description": a.description,
                    "prompt": a.prompt,
                    "variant": a.variant,
                    "temperature": a.temperature,
                    "top_p": a.top_p,
                    "color": a.color,
                    "hidden": a.hidden,
                    "native": a.native,
                    "mode": a.mode,
                    "options": a.options,
                }
                for name, a in config.agents.items()
            },
        }

    def _dict_to_config(self, data: dict) -> LunaConfig:
        """Convert dictionary to LunaConfig."""
        config = LunaConfig(
            active_agent=data.get("active_agent", "build"),
            default_model=data.get("default_model"),
            theme=data.get("theme", "neon"),
            locale=data.get("locale", "en"),
        )

        for name, a in data.get("agents", {}).items():
            config.agents[name] = AgentConfig(**a)

        return config

    def save(self) -> None:
        """Save config to YAML file."""
        data = self._config_to_dict(self.config)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.dump(data, default_flow_style=False))
        with contextlib.suppress(OSError):
            os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)

    @property
    def active_agent(self) -> str:
        return self.config.active_agent

    @active_agent.setter
    def active_agent(self, name: str) -> None:
        if name in self.config.agents:
            self.config.active_agent = name
            self.save()

    def get_agent(self, name: str) -> AgentConfig | None:
        return self.config.agents.get(name)

    def get_all_agents(self) -> dict[str, AgentConfig]:
        return self.config.agents.copy()

    def set_agent(self, name: str, config: AgentConfig) -> None:
        self.config.agents[name] = config
        self.save()

    def remove_agent(self, name: str) -> bool:
        if name in self.config.agents:
            del self.config.agents[name]
            self.save()
            return True
        return False

    def get_model_for_agent(self, agent_name: str) -> str | None:
        """Get model override for a specific agent."""
        agent = self.config.agents.get(agent_name)
        if agent and agent.model:
            return agent.model
        return None

    def get_agent_permissions(self, agent_name: str) -> dict[str, str]:
        """Get permissions for an agent."""
        agent = self.config.agents.get(agent_name)
        return agent.permissions if agent else {}

    def list_agents(self) -> list[dict]:
        """List all agents with their info."""
        result = []
        for name, a in self.config.agents.items():
            result.append({
                "name": name,
                "model": a.model,
                "description": a.description,
                "native": a.native,
                "mode": a.mode,
                "hidden": a.hidden,
            })
        return result
