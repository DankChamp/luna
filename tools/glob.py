from __future__ import annotations
from pathlib import Path

from .registry import ToolDef


async def glob_files(pattern: str, path: str | None = None) -> str:
    search_dir = Path(path).expanduser().resolve() if path else Path.cwd()
    if not search_dir.exists():
        return f"Error: directory not found: {path}"

    try:
        matches = [str(p) for p in search_dir.rglob(pattern)]
    except Exception as e:
        return f"Error globbing: {e}"

    if not matches:
        return f"No files matching '{pattern}' found in {search_dir}"
    return "\n".join(sorted(matches)[:200])


glob_tool = ToolDef(
    name="glob",
    description="Search for files matching a glob pattern. Supports **/*.py, *.txt, src/**/*.ts",
    parameters={
        "pattern": {
            "type": "string",
            "description": "Glob pattern to match files (e.g. **/*.py)",
        },
        "path": {
            "type": "string",
            "description": "Directory to search in (defaults to current directory)",
        },
    },
    required=["pattern"],
    handler=glob_files,
)
