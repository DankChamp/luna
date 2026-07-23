from __future__ import annotations
import asyncio

from .registry import ToolDef


async def grep_content(pattern: str, path: str | None = None, include: str | None = None) -> str:
    cmd = ["rg", "-n", pattern]
    if include:
        cmd.extend(["--include", f"*.{include}" if "." not in include else include])
    if path:
        cmd.append(path)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return "Error: grep timed out after 30s"
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found. Install with: sudo apt install ripgrep"
    except Exception as e:
        return f"Error running grep: {e}"

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")

    result = ""
    if out:
        lines = out.splitlines()
        result = "\n".join(lines[:100])
        if len(lines) > 100:
            result += f"\n... ({len(lines) - 100} more matches)"
    if err:
        result += f"\n[stderr] {err}"

    return result if result else f"No matches for '{pattern}'"


grep_tool = ToolDef(
    name="grep",
    description="Search file contents using regex via ripgrep (rg).",
    parameters={
        "pattern": {
            "type": "string",
            "description": "Regex pattern to search for",
        },
        "path": {
            "type": "string",
            "description": "Directory to search in",
        },
        "include": {
            "type": "string",
            "description": "File pattern to filter (e.g. py, js, *.ts)",
        },
    },
    required=["pattern"],
    handler=grep_content,
)
