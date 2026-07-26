from __future__ import annotations
import asyncio

from .registry import ToolDef


async def _run_gh(args: list[str], timeout: int = 30) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        result = out
        if proc.returncode != 0:
            result = f"[exit code {proc.returncode}] "
            result += err if err else out
            return result if result.strip() else f"(exited with code {proc.returncode})"
        if err:
            result += f"\n[stderr] {err}"
        return result if result else "(no output)"
    except FileNotFoundError:
        return "Error: gh CLI not found. Install from https://cli.github.com/"
    except asyncio.TimeoutError:
        return "Error: gh command timed out"
    except Exception as e:
        return f"Error: {e}"


async def gh_create_pr(title: str, body: str = "", base: str = "", draft: bool = False) -> str:
    args = ["pr", "create", "--title", title]
    if body:
        args.extend(["--body", body])
    if base:
        args.extend(["--base", base])
    if draft:
        args.append("--draft")
    return await _run_gh(args)


async def gh_create_issue(title: str, body: str = "", label: str = "") -> str:
    args = ["issue", "create", "--title", title]
    if body:
        args.extend(["--body", body])
    if label:
        args.extend(["--label", label])
    return await _run_gh(args)


async def gh_list_prs(state: str = "open", limit: int = 10) -> str:
    return await _run_gh(["pr", "list", "--state", state, f"--limit={limit}"])


async def gh_list_issues(state: str = "open", limit: int = 10) -> str:
    return await _run_gh(["issue", "list", "--state", state, f"--limit={limit}"])


pr_tool = ToolDef(
    name="create_pr",
    description="Create a GitHub Pull Request using the gh CLI.",
    parameters={
        "title": {"type": "string", "description": "PR title"},
        "body": {"type": "string", "description": "PR body/description"},
        "base": {"type": "string", "description": "Target branch (default: repo default)"},
        "draft": {"type": "boolean", "description": "Create as draft PR"},
    },
    required=["title"],
    handler=gh_create_pr,
)

issue_tool = ToolDef(
    name="create_issue",
    description="Create a GitHub Issue using the gh CLI.",
    parameters={
        "title": {"type": "string", "description": "Issue title"},
        "body": {"type": "string", "description": "Issue body"},
        "label": {"type": "string", "description": "Comma-separated labels"},
    },
    required=["title"],
    handler=gh_create_issue,
)

list_prs_tool = ToolDef(
    name="list_prs",
    description="List GitHub Pull Requests.",
    parameters={
        "state": {"type": "string", "description": "State: open, closed, merged, all"},
        "limit": {"type": "integer", "description": "Max results"},
    },
    handler=gh_list_prs,
)

list_issues_tool = ToolDef(
    name="list_issues",
    description="List GitHub Issues.",
    parameters={
        "state": {"type": "string", "description": "State: open, closed, all"},
        "limit": {"type": "integer", "description": "Max results"},
    },
    handler=gh_list_issues,
)
