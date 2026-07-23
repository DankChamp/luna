from __future__ import annotations
import asyncio

from .registry import ToolDef


async def run_command(command: str, timeout: int = 120000) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
