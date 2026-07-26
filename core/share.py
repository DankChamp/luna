from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def format_session(messages: list[dict], title: str = "Luna Session") -> str:
    lines = [f"# {title}", f"Exported: {datetime.now(timezone.utc).isoformat()}", ""]
    for m in messages:
        role = m["role"].upper()
        content = m.get("content", "")
        tool_calls = m.get("tool_calls")
        if not content and not tool_calls:
            continue
        if role == "SYSTEM":
            lines.append(f"> {content}")
            lines.append("")
        elif role == "USER":
            lines.append(f"## {role}")
            lines.append(content)
            lines.append("")
        elif role == "ASSISTANT":
            lines.append(f"## {role}")
            if content:
                lines.append(content)
                lines.append("")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", "")
                    lines.append(f"  → tool: {name}")
                    lines.append(f"    ```json\n{args}\n    ```")
                lines.append("")
        elif role == "TOOL":
            tool_id = m.get("tool_call_id", "")[:8]
            lines.append(f"### Tool Result ({tool_id})")
            lines.append(f"```\n{content[:500]}\n```")
            lines.append("")
    return "\n".join(lines)


async def paste_to_ix(text: str) -> Optional[str]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("https://ix.io", data={"f:1": text})
            if resp.status_code == 200:
                return resp.text.strip()
            return None
    except Exception:
        return None
