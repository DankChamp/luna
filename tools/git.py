from __future__ import annotations
import asyncio

from .registry import ToolDef


async def run_git(args: list[str], timeout: int = 30) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return "Error: git command timed out"
    except FileNotFoundError:
        return "Error: git not found"
    except Exception as e:
        return f"Error: {e}"

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        msg = f"[exit code {proc.returncode}]"
        if err:
            msg += " " + err.strip()
        elif out:
            msg += " " + out.strip()
        return msg

    result = ""
    if out:
        result += out
    if err:
        if result:
            result += "\n"
        result += err
    return result if result else "(no output)"


async def git_status(path: str = ".") -> str:
    return await run_git(["-C", path, "status", "--short"])


async def git_diff(path: str = ".", staged: bool = False) -> str:
    args = ["-C", path, "diff"]
    if staged:
        args.append("--staged")
    return await run_git(args, timeout=30)


async def git_log(path: str = ".", count: int = 10) -> str:
    return await run_git(
        ["-C", path, "log", f"-{count}", "--oneline", "--graph", "--decorate"],
        timeout=15,
    )


async def git_commit(message: str, add_all: bool = True, path: str = ".") -> str:
    if add_all:
        add_result = await run_git(["-C", path, "add", "-A"], timeout=10)
        if add_result and "Error" in add_result:
            return add_result
    return await run_git(["-C", path, "commit", "-m", message], timeout=15)


async def git_push(remote: str = "origin", branch: str = "", path: str = ".") -> str:
    args = ["-C", path, "push", remote]
    if branch:
        args.append(branch)
    return await run_git(args, timeout=60)


git_status_tool = ToolDef(
    name="git_status",
    description="Show working tree status (git status --short)",
    parameters={
        "path": {
            "type": "string",
            "description": "Repository path (default: current directory)",
        },
    },
    handler=git_status,
)

git_diff_tool = ToolDef(
    name="git_diff",
    description="Show uncommitted changes (git diff)",
    parameters={
        "path": {"type": "string", "description": "Repository path"},
        "staged": {"type": "boolean", "description": "Show staged changes only"},
    },
    handler=git_diff,
)

git_log_tool = ToolDef(
    name="git_log",
    description="Show recent commits (git log)",
    parameters={
        "path": {"type": "string", "description": "Repository path"},
        "count": {"type": "integer", "description": "Number of commits to show"},
    },
    handler=git_log,
)

git_commit_tool = ToolDef(
    name="git_commit",
    description="Stage all changes and commit (git add -A + git commit)",
    parameters={
        "message": {"type": "string", "description": "Commit message"},
        "add_all": {"type": "boolean", "description": "Stage all changes first"},
        "path": {"type": "string", "description": "Repository path"},
    },
    required=["message"],
    handler=git_commit,
)

git_push_tool = ToolDef(
    name="git_push",
    description="Push to remote (git push)",
    parameters={
        "remote": {"type": "string", "description": "Remote name (default: origin)"},
        "branch": {"type": "string", "description": "Branch to push"},
        "path": {"type": "string", "description": "Repository path"},
    },
    handler=git_push,
)
