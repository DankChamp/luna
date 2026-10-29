"""Core utilities and shared functionality."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.errors import ConfigError, ConfigMigrationError, PluginError, ProviderError, SessionError
from core.logging import get_contextual_logger, get_logger, setup_logging
from core.modes import MODE_INDICATORS, MODE_PROMPTS, MODE_TOOL_BLOCKS, AgentMode
from core.persona import load_luna_persona
from core.protocols import (
    AgentCoreProtocol,
    AIProviderProtocol,
    ConfigManagerProtocol,
    FormatCallback,
    LSPDiagnosticsCallback,
    Message,
    Messages,
    ProviderConfig,
    ProviderGetter,
    ProviderNameGetter,
    ReferenceManagerProtocol,
    RouterProtocol,
    SessionManagerProtocol,
    SkillManagerProtocol,
    SubagentManagerProtocol,
    ToolCallResults,
    ToolCalls,
    ToolExecutorProtocol,
    ToolHandler,
    ToolRegistryProtocol,
)


def _xdg(env_var: str, default: str) -> Path:
    raw = os.environ.get(env_var, "").strip()
    base = Path(raw) if raw else Path.home() / default
    return base


def config_home() -> Path:
    """e.g. ~/.config/luna"""
    return _xdg("XDG_CONFIG_HOME", ".config") / "luna"


def data_home() -> Path:
    """e.g. ~/.local/share/luna"""
    return _xdg("XDG_DATA_HOME", ".local/share") / "luna"


def state_home() -> Path:
    """e.g. ~/.local/state/luna"""
    return _xdg("XDG_STATE_HOME", ".local/state") / "luna"


def legacy_home() -> Path:
    """The original ~/.luna, kept as a fallback search location."""
    return Path.home() / ".luna"


def search_dirs(subpath: str) -> list[str]:
    """Ordered dirs to look in for a given subpath (e.g. 'persona', 'skills'),
    XDG-correct location first, legacy ~/.luna/<subpath> as fallback."""
    return [
        str(config_home() / subpath),
        str(legacy_home() / subpath),
    ]


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_workspace_root() -> Path:
    """Get the workspace root for path validation.
    Falls back to current working directory if no git repo found."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except Exception:
        pass
    return Path.cwd().resolve()


def validate_path_within_workspace(path: str | Path, workspace_root: Path | None = None) -> Path:
    """Validate that a path is within the workspace root to prevent path traversal.

    Args:
        path: The path to validate (can be relative or absolute)
        workspace_root: Optional workspace root. If not provided, uses git root or CWD.

    Returns:
        Resolved Path object if valid.

    Raises:
        ValueError: If path attempts to escape workspace root.
    """
    if workspace_root is None:
        workspace_root = _get_workspace_root()

    workspace_root = workspace_root.resolve()
    path_obj = Path(path).expanduser()

    # If path is relative, resolve it against workspace_root
    if not path_obj.is_absolute():
        path_obj = (workspace_root / path_obj).resolve()
    else:
        path_obj = path_obj.resolve()

    try:
        path_obj.relative_to(workspace_root)
    except ValueError as err:
        raise ValueError(f"Path '{path}' is outside workspace root '{workspace_root}'") from err

    return path_obj
