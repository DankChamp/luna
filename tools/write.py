from __future__ import annotations
from pathlib import Path

from core.paths import validate_path_within_workspace
from .registry import ToolDef


async def write_file(path: str, content: str) -> str:
    try:
        p = validate_path_within_workspace(path)
    except ValueError as e:
        return f"Error: {e}"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Atomic write: write to temp file then rename
        temp_path = p.with_suffix(p.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(p)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


write_tool = ToolDef(
    name="write",
    description="Write content to a file, creating directories if needed. Overwrites existing content. Path must be within workspace.",
    parameters={
        "path": {
            "type": "string",
            "description": "Path to the file to write (relative to workspace root)",
        },
        "content": {
            "type": "string",
            "description": "Full content to write to the file",
        },
    },
    required=["path", "content"],
    handler=write_file,
)
