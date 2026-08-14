from __future__ import annotations
from pathlib import Path

from core.paths import validate_path_within_workspace, _get_workspace_root
from .registry import ToolDef


async def glob_files(pattern: str, path: str | None = None) -> str:
    try:
        if path:
            search_dir = validate_path_within_workspace(path)
            if not search_dir.is_dir():
                return f"Error: not a directory: {path}"
        else:
            search_dir = _get_workspace_root()
    except ValueError as e:
        return f"Error: {e}"
    if not search_dir.exists():
        return f"Error: directory not found: {path}"

    try:
        # Don't follow symlinks to prevent escaping workspace
        matches = [str(p) for p in search_dir.rglob(pattern) if not p.is_symlink()]
    except Exception as e:
        return f"Error globbing: {e}"

    if not matches:
        return f"No files matching '{pattern}' found in {search_dir}"
    return "\n".join(sorted(matches)[:200])


glob_tool = ToolDef(
    name="glob",
    description="Search for files matching a glob pattern. Supports **/*.py, *.txt, src/**/*.ts. Path must be within workspace.",
    parameters={
        "pattern": {
            "type": "string",
            "description": "Glob pattern to match files (e.g. **/*.py)",
        },
        "path": {
            "type": "string",
            "description": "Directory to search in (defaults to workspace root)",
        },
    },
    required=["pattern"],
    handler=glob_files,
)
