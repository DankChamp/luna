from __future__ import annotations
import asyncio
import os
import shlex
import shutil
import re
from pathlib import Path
from typing import Optional

from .registry import ToolDef


# Use bash if available for compatibility with model-generated commands
_BASH_PATH = shutil.which("bash") or "/bin/sh"


# Commands that change directory
_CWD_COMMANDS = {"cd", "chdir", "pushd", "popd", "push-location", "set-location"}

# File operations that affect filesystem
_FILE_COMMANDS = {
    "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown",
    "cat", "get-content", "set-content", "add-content",
    "copy-item", "move-item", "remove-item", "new-item", "rename-item",
    "copy", "del", "dir", "erase", "md", "move", "rd", "ren", "rmdir", "type",
}

# Shell metacharacters that require bash -c
_SHELL_META_CHARS = "|&;<>()$`\\\"'"


def _parse_cwd_changes(command: str) -> list[str]:
    """Extract potential cd/chdir commands to track CWD changes."""
    # Simple heuristic: find cd/chdir commands and extract their arguments
    cd_pattern = r'\b(cd|chdir|pushd)\s+([^\s|&;]+)'
    matches = re.findall(cd_pattern, command)
    return [m[1].strip('"\'') for m in matches]


def _has_shell_meta(command: str) -> bool:
    """Check if command contains shell metacharacters."""
    return any(c in command for c in _SHELL_META_CHARS)


def _is_cwd_command(argv: list[str]) -> bool:
    """Check if command is a CWD-changing command."""
    if not argv:
        return False
    return argv[0] in _CWD_COMMANDS


def _is_file_command(argv: list[str]) -> bool:
    """Check if command is a file operation."""
    if not argv:
        return False
    return argv[0] in _FILE_COMMANDS


class BashToolState:
    """State for bash tool including CWD tracking."""
    _cwd: str = os.getcwd()
    _cwd_stack: list[str] = []

    @classmethod
    def get_cwd(cls) -> str:
        return cls._cwd

    @classmethod
    def set_cwd(cls, path: str) -> None:
        cls._cwd = str(Path(path).expanduser().resolve())

    @classmethod
    def push_cwd(cls, path: str) -> None:
        cls._cwd_stack.append(cls._cwd)
        cls.set_cwd(path)

    @classmethod
    def pop_cwd(cls) -> str | None:
        if cls._cwd_stack:
            cls._cwd = cls._cwd_stack.pop()
            return cls._cwd
        return None


async def run_command(
    command: str,
    timeout: int = 120000,
    cwd: str | None = None,
) -> str:
    """Run a shell command safely with CWD tracking and enhanced parsing."""
    working_cwd = cwd or BashToolState.get_cwd()

    try:
        # Parse command string into argv list (handles quotes, escapes)
        argv = shlex.split(command, posix=True)
        if not argv:
            return "Error: empty command"

        # Track CWD changes from cd/pushd/popd
        if _is_cwd_command(argv):
            if argv[0] in ("pushd", "push-location"):
                if len(argv) > 1:
                    new_cwd = str(Path(working_cwd) / argv[1]).resolve()
                    BashToolState.push_cwd(new_cwd)
                    working_cwd = new_cwd
                else:
                    # pushd without args swaps with top of stack
                    BashToolState.push_cwd(BashToolState.get_cwd())
            elif argv[0] in ("popd", "pop-location"):
                BashToolState.pop_cwd()
            elif argv[0] in ("cd", "chdir"):
                if len(argv) > 1:
                    new_cwd = str(Path(working_cwd) / argv[1]).resolve()
                    BashToolState.set_cwd(new_cwd)
                    working_cwd = new_cwd
                else:
                    # cd without args goes to home
                    BashToolState.set_cwd(str(Path.home()))
                    working_cwd = str(Path.home())

        # Prepend bash -c if command contains shell features (pipes, redirects, etc.)
        # This maintains compatibility while avoiding direct shell interpretation
        shell_meta = _has_shell_meta(command)
        if shell_meta:
            argv = [_BASH_PATH, "-c", command]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_cwd,
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


async def run_command_interactive(
    command: str,
    timeout: int = 120000,
    cwd: str | None = None,
) -> str:
    """Run a command with interactive output streaming."""
    working_cwd = cwd or BashToolState.get_cwd()

    try:
        argv = shlex.split(command, posix=True)
        if not argv:
            return "Error: empty command"

        shell_meta = _has_shell_meta(command)
        if shell_meta:
            argv = [_BASH_PATH, "-c", command]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_cwd,
        )

        output_lines = []
        try:
            async for line in proc.stdout:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                output_lines.append(line_str)
                print(line_str)  # Stream to console

            async for line in proc.stderr:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                output_lines.append(f"[stderr] {line_str}")
                print(f"[stderr] {line_str}")

            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: command timed out after {timeout}ms"

        result = "\n".join(output_lines)
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
    description=(
        "Run a shell command. Use for: creating directories (mkdir -p), "
        "running scripts, git, build tools, or any terminal operation. "
        "Commands are executed safely without shell injection vulnerabilities. "
        "Supports CWD tracking for cd/pushd/popd."
    ),
    parameters={
        "command": {
            "type": "string",
            "description": "The shell command to run (e.g., 'mkdir -p path/to/dir', 'ls -la', 'git status'). Shell features like pipes/redirects supported via bash -c.",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in milliseconds (default 120000)",
        },
        "cwd": {
            "type": "string",
            "description": "Working directory (optional, uses tracked CWD if not specified)",
        },
    },
    required=["command"],
    handler=run_command,
)