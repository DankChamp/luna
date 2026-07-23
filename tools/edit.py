from __future__ import annotations
from pathlib import Path

from .registry import ToolDef


async def edit_file(path: str, old_string: str, new_string: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    count = text.count(old_string)
    if count == 0:
        return f"Error: string not found in {path}"
    if count > 1:
        return f"Error: found {count} matches in {path}. Provide more context."

    text = text.replace(old_string, new_string, 1)
    try:
        p.write_text(text, encoding="utf-8")
        return f"Applied edit to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


edit_tool = ToolDef(
    name="edit",
    description="Edit a file by finding and replacing exact text. Use instead of write for targeted changes.",
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute path to the file to edit",
        },
        "old_string": {
            "type": "string",
            "description": "The exact text to find (must match exactly)",
        },
        "new_string": {
            "type": "string",
            "description": "The replacement text",
        },
    },
    required=["path", "old_string", "new_string"],
    handler=edit_file,
)
