from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .registry import ToolDef, ToolRegistry


# Allowlist of safe builtins for custom tools
_SAFE_BUILTINS = {
    "len", "str", "int", "float", "bool", "list", "dict", "tuple", "set",
    "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "min", "max", "sum", "any", "all", "isinstance", "issubclass",
    "hasattr", "getattr", "setattr", "type", "object", "Exception",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "RuntimeError", "OSError", "IOError", "FileNotFoundError",
}

# Blocked imports for security
_BLOCKED_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "pathlib", "importlib",
    "socket", "urllib", "http", "requests", "httpx", "ftplib",
    "pickle", "marshal", "shelve", "dbm", "sqlite3",
    "threading", "multiprocessing", "asyncio", "concurrent",
    "ctypes", "cffi", "importlib.util", "importlib.machinery",
    "pkgutil", "runpy", "code", "codeop",
}


def _create_safe_globals() -> dict:
    """Create a restricted globals dictionary for custom tool execution."""
    safe_globals = {name: getattr(__builtins__, name) for name in _SAFE_BUILTINS}
    safe_globals["__builtins__"] = safe_globals
    safe_globals["__name__"] = "custom_tool"
    safe_globals["__file__"] = "<custom_tool>"
    return safe_globals


def _safe_import(name: str, *args, **kwargs):
    """Restricted import function that blocks dangerous modules."""
    if name in _BLOCKED_IMPORTS:
        raise ImportError(f"Import of '{name}' is blocked for security")
    # Allow safe standard library modules
    safe_modules = {
        "json", "re", "datetime", "time", "math", "random", "statistics",
        "collections", "itertools", "functools", "operator", "string",
        "textwrap", "hashlib", "hmac", "base64", "uuid", "dataclasses",
        "typing", "decimal", "fractions", "numbers", "copy", "pprint",
    }
    if name in safe_modules or name.startswith(("json.", "re.", "datetime.")):
        return __import__(name, *args, **kwargs)
    raise ImportError(f"Import of '{name}' is not allowed")


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
                # Inject safe globals before execution
                mod.__dict__.update(_create_safe_globals())
                mod.__dict__["__import__"] = _safe_import
                spec.loader.exec_module(mod)
                for attr in dir(mod):
                    val = getattr(mod, attr)
                    if isinstance(val, ToolDef) and val.name not in seen_tool_names:
                        tools.append(val)
                        seen_tool_names.add(val.name)
            except Exception:
                # Silently skip invalid custom tools
                pass

    return tools


def register_custom_tools(registry: ToolRegistry, *search_dirs: str | Path):
    for tool in discover_custom_tools(*search_dirs):
        registry.register(tool)
