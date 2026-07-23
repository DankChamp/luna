from __future__ import annotations
from pathlib import Path

from .registry import ToolDef


async def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        return f"Error reading file: {e}"

    start = max(0, offset - 1) if offset > 0 else 0
    end = start + limit if limit else len(lines)
    selected = lines[start:end]

    result = []
    for i, line in enumerate(selected, start=start + 1):
        result.append(f"{i}: {line}")

    meta = []
    if offset > 0:
        meta.append(f"offset={start + 1}")
    if limit and len(lines) > end:
        meta.append(f"showing {len(selected)} of {len(lines)} lines")
    elif limit and len(lines) <= end:
        meta.append(f"{len(lines)} lines")

    out = "".join(result)
    if meta:
        out = f"[{'; '.join(meta)}]\n{out}"
    return out if out else "(empty file)"


read_tool = ToolDef(
    name="read",
    description="Read a file. Use offset to start from a specific line, limit to control how many lines.",
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute path to the file to read",
        },
        "offset": {
            "type": "integer",
            "description": "Line number to start reading from (1-indexed)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of lines to read",
        },
    },
    required=["path"],
    handler=read_file,
)
