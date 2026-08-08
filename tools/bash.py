from __future__ import annotations
import asyncio
import os
import shutil

from .registry import ToolDef

# asyncio.create_subprocess_shell uses /bin/sh by default on POSIX, which on
# many Linux distros is dash — no `[[`, no `pipefail`, no process substitution.
# Models write bash, so give them real bash when it's on the system.
_SHELL = shutil.which("bash") or "/bin/sh"


async def run_command(command: str, timeout: int = 120000) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            executable=_SHELL,
            cwd=os.getcwd(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout / 1000
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: command timed out after {timeout}ms"

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        result = ""
        if out:
            result += out
        if err:
            if result:
                result += "\n"
            result += f"[stderr]\n{err}"
        if proc.returncode != 0:
            result += f"\n[exit code: {proc.returncode}]"

        limit = 100 * 1024
        if len(result) > limit:
            result = result[:limit] + f"\n... (truncated, {len(result)} total bytes)"

        return result if result else "(no output)"
    except Exception as e:
        return f"Error running command: {e}"


bash_tool = ToolDef(
    name="bash",
    description="Run a shell command on the system. For building, testing, git, or terminal operations.",
    parameters={
        "command": {
            "type": "string",
            "description": "The shell command to run",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in milliseconds (default 120000)",
        },
    },
    required=["command"],
    handler=run_command,
)
