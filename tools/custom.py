from __future__ import annotations
import importlib.util
from pathlib import Path

from .registry import ToolDef, ToolRegistry


def discover_custom_tools(*search_dirs: str | Path) -> list[ToolDef]:
    tools: list[ToolDef] = []
    seen_files: set[str] = set()
    seen_tool_names: set[str] = set()

    for d in search_dirs:
        sd = Path(d).expanduser().resolve()
        if not sd.exists():
            continue
        for f in sorted(sd.iterdir()):
            if f.suffix != ".py" or f.stem == "__init__":
                continue
            if f.stem in seen_files:
                continue
            seen_files.add(f.stem)
            try:
                spec = importlib.util.spec_from_file_location(f.stem, f)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr in dir(mod):
                    val = getattr(mod, attr)
                    if isinstance(val, ToolDef) and val.name not in seen_tool_names:
                        tools.append(val)
                        seen_tool_names.add(val.name)
            except Exception:
                pass

    return tools


def register_custom_tools(registry: ToolRegistry, *search_dirs: str | Path):
    for tool in discover_custom_tools(*search_dirs):
        registry.register(tool)
