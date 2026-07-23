from __future__ import annotations
from pathlib import Path

from .registry import ToolDef


async def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


write_tool = ToolDef(
    name="write",
    description="Write content to a file, creating directories if needed. Overwrites existing content.",
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute path to the file to write",
        },
        "content": {
            "type": "string",
            "description": "Full content to write to the file",
        },
    },
    required=["path", "content"],
    handler=write_file,
)
