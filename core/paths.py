"""XDG Base Directory support for Luna's on-disk state.

Historically Luna hard-codes everything under ~/.luna. That's fine and still
works, but on Linux the convention (and what tools like `restic`, `bat`, and
most Rust/Go CLIs do) is to split things across:

  $XDG_CONFIG_HOME (~/.config)   — persona, skills, commands, themes, keybinds
  $XDG_DATA_HOME   (~/.local/share) — sessions, memory, history
  $XDG_STATE_HOME  (~/.local/state) — logs

Rather than force a breaking migration, each accessor below returns the
XDG-correct path first, but callers that build search-dir lists should still
append the legacy ~/.luna/<name> path as a fallback so existing users' data
keeps working untouched. New installs land in the XDG-correct spot.
"""
from __future__ import annotations
import os
from pathlib import Path


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
    """The original ~/.luna, kept as a fallback search location so nothing
    breaks for people who've already been using Luna."""
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
