from __future__ import annotations
from pathlib import Path

from core.references import ReferenceManager
from .registry import ToolDef


def create_reference_tool(ref_mgr: ReferenceManager) -> ToolDef:
    async def read_reference(alias: str, path: str = "") -> str:
        ref = ref_mgr.get(alias)
        if not ref:
            return f"Error: unknown reference '{alias}'"

        if not ref.resolved_path:
            return f"Error: reference '{alias}' has no local path"

        target = ref.resolved_path
        if path:
            target = target / path

        if not target.exists():
            return f"Error: path not found in reference '{alias}': {target}"

        if target.is_file():
            try:
                return target.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"Error reading file: {e}"

        if target.is_dir():
            try:
                entries = list(target.iterdir())
                lines = [f"Contents of @{alias}/{path}:"]
                for e in sorted(entries):
                    suffix = "/" if e.is_dir() else ""
                    lines.append(f"  {e.name}{suffix}")
                return "\n".join(lines)
            except Exception as e:
                return f"Error listing directory: {e}"

        return f"Error: path not found: {target}"

    visible_refs = [r for r in ref_mgr.list_refs() if not r.hidden]
    if visible_refs:
        desc = "Read files or list directories from configured references. Available references: " + ", ".join(r.alias for r in visible_refs)
    else:
        desc = "Read files or list directories from configured references."
    return ToolDef(
        name="read_reference",
        description=desc,
        parameters={
            "alias": {
                "type": "string",
                "description": "Reference alias (e.g. docs, sdk)",
            },
            "path": {
                "type": "string",
                "description": "Path within the reference (empty to list root)",
            },
        },
        required=["alias"],
        handler=read_reference,
    )
