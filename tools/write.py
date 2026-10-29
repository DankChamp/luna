from __future__ import annotations

from core import validate_path_within_workspace

from .registry import ToolDef


async def write_file(path: str | None = None, content: str = "", filename: str | None = None) -> str:
    # Support both 'path' and 'filename' for model compatibility
    file_path = path or filename
    if not file_path:
        return "Error: Either 'path' or 'filename' parameter is required"
    
    try:
        p = validate_path_within_workspace(file_path)
    except ValueError as e:
        return f"Error: {e}"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Atomic write: write to temp file then rename
        temp_path = p.with_suffix(p.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(p)
        return f"Wrote {len(content)} bytes to {file_path}"
    except Exception as e:
        return f"Error writing file: {e}"


write_tool = ToolDef(
    name="write",
    description="Write content to a FILE (not a directory). Creates parent directories if needed. Overwrites existing content. Path must be within workspace.",
    parameters={
        "path": {
            "type": "string",
            "description": "Path to the FILE to write (relative to workspace root)",
        },
        "filename": {
            "type": "string",
            "description": "Alternative parameter name for path (for model compatibility)",
        },
        "content": {
            "type": "string",
            "description": "Full content to write to the file",
        },
    },
    required=["content"],
    handler=write_file,
)
