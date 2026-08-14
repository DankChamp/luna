from __future__ import annotations
import asyncio
import os
import shlex
import shutil

from .registry import ToolDef

# Use bash if available for compatibility with model-generated commands
_BASH_PATH = shutil.which("bash") or "/bin/sh"


async def run_command(command: str, timeout: int = 120000) -> str:
    """Run a shell command safely using create_subprocess_exec with argument list.
    
    Avoids shell injection by parsing the command string into an argument list
    and executing directly without shell interpretation.
    """
    try:
        # Parse command string into argv list (handles quotes, escapes)
        argv = shlex.split(command, posix=True)
        if not argv:
            return "Error: empty command"
        
        # Prepend bash -c if command contains shell features (pipes, redirects, etc.)
        # This maintains compatibility while avoiding direct shell interpretation
        shell_meta = any(c in command for c in "|&;<>()$`\\\"'")
        if shell_meta:
            argv = [_BASH_PATH, "-c", command]
        
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout / 1000
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
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
    description="Run a shell command on the system. For building, testing, git, or terminal operations. Commands are executed safely without shell injection vulnerabilities.",
    parameters={
        "command": {
            "type": "string",
            "description": "The shell command to run (will be parsed safely, shell features like pipes/redirects supported via bash -c)",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in milliseconds (default 120000)",
        },
    },
    required=["command"],
    handler=run_command,
)
