from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


BUILTIN_THEMES: dict[str, dict] = {
    "neon": {
        "primary": "#ff00ff",
        "secondary": "#00ffff",
        "success": "#00ff41",
        "error": "#ff0055",
        "warning": "#ffaa00",
        "dim": "#666666",
        "accent": "#8800ff",
        "bright": "#ffffff",
    },
    "tokyonight": {
        "primary": "#7aa2f7",
        "secondary": "#bb9af7",
        "success": "#9ece6a",
        "error": "#f7768e",
        "warning": "#e0af68",
        "dim": "#565f89",
        "accent": "#7dcfff",
        "bright": "#a9b1d6",
    },
    "nord": {
        "primary": "#88c0d0",
        "secondary": "#81a1c1",
        "success": "#a3be8c",
        "error": "#bf616a",
        "warning": "#d08770",
        "dim": "#4c566a",
        "accent": "#b48ead",
        "bright": "#d8dee9",
    },
    "catppuccin": {
        "primary": "#cba6f7",
        "secondary": "#89b4fa",
        "success": "#a6e3a1",
        "error": "#f38ba8",
        "warning": "#fab387",
        "dim": "#585b70",
        "accent": "#94e2d5",
        "bright": "#cdd6f4",
    },
    "matrix": {
        "primary": "#00ff41",
        "secondary": "#00cc33",
        "success": "#00ff41",
        "error": "#ff0033",
        "warning": "#ffcc00",
        "dim": "#003300",
        "accent": "#00ff88",
        "bright": "#00ff00",
    },
}


@dataclass
class ThemeColors:
    primary: str = "#ff00ff"
    secondary: str = "#00ffff"
    success: str = "#00ff41"
    error: str = "#ff0055"
    warning: str = "#ffaa00"
    dim: str = "#666666"
    accent: str = "#8800ff"
    bright: str = "#ffffff"

    def apply(self, target: object):
        for key in ("primary", "secondary", "success", "error", "warning", "dim", "accent", "bright"):
            val = getattr(self, key, None)
            if val:
                setattr(target, key, val)


class ThemeManager:
    def __init__(self):
        self._themes: dict[str, ThemeColors] = {}
        for name, colors in BUILTIN_THEMES.items():
            self._themes[name] = ThemeColors(**{k: v for k, v in colors.items() if hasattr(ThemeColors, k)})
        self._current: str = "neon"
        self._load_custom()

    def _load_custom(self):
        for d in [Path.home() / ".luna" / "themes", Path.cwd() / ".luna" / "themes"]:
            if not d.exists():
                continue
            for f in sorted(d.iterdir()):
                if f.suffix == ".json" and f.is_file():
                    self._load_file(f)

    def _load_file(self, path: Path):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            colors = raw.get("theme", raw.get("colors", raw))
            defs = raw.get("defs", {})
            resolved = {}
            for key in ("primary", "secondary", "success", "error", "warning", "dim", "accent", "bright"):
                val = colors.get(key)
                if val is None:
                    continue
                if isinstance(val, dict):
                    val = val.get("dark", val.get("light"))
                if isinstance(val, str) and val.startswith("#"):
                    resolved[key] = val
                elif isinstance(val, str) and val in defs:
                    ref = defs[val]
                    if isinstance(ref, dict):
                        ref = ref.get("dark", ref.get("light"))
                    if isinstance(ref, str) and ref.startswith("#"):
                        resolved[key] = ref
            if resolved:
                name = path.stem
                self._themes[name] = ThemeColors(**resolved)
        except Exception:
            pass

    def list(self) -> list[str]:
        return list(self._themes.keys())

    def get(self, name: str) -> ThemeColors | None:
        return self._themes.get(name)

    def activate(self, name: str, target: object) -> bool:
        theme = self._themes.get(name)
        if not theme:
            return False
        theme.apply(target)
        self._current = name
        return True

    @property
    def current(self) -> str:
        return self._current
