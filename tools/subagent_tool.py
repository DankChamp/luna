from __future__ import annotations

from pathlib import Path

from core.subagents import SubagentManager, AgentDef
from .registry import ToolDef


def create_subagent_tool(mgr: SubagentManager) -> ToolDef:
    async def _(
        name: str,
        description: str,
        prompt: str,
        tools: list[str] | None = None,
        color: str = "#ff00ff",
    ) -> str:
        agent_def = AgentDef(
            name=name,
            description=description,
            prompt=prompt,
            tools=tools,
            color=color,
        )
        mgr.register(agent_def)

        save_dir = Path.home() / ".luna" / "subagents"
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{name}.md"

        tools_line = ""
        if tools:
            tools_line = f"tools: {', '.join(tools)}\n"

        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"{tools_line}"
            f"color: {color}\n"
            f"---\n"
            f"\n"
            f"{prompt}\n"
        )
        path.write_text(frontmatter, encoding="utf-8")

        return (
            f"Created sub-agent '{name}' and saved to {path}\n"
            f"Description: {description}\n"
            f"Tools: {', '.join(tools) if tools else 'all'}"
        )

    return ToolDef(
        name="create_subagent",
        description=(
            "Create a new sub-agent at runtime with a custom name, description, "
            "system prompt, and optional tool restrictions. "
            "The sub-agent is persisted to disk and can be reused later. "
            "After creation, use the task tool to run it."
        ),
        parameters={
            "name": {
                "type": "string",
                "description": "Unique name for the sub-agent",
            },
            "description": {
                "type": "string",
                "description": "Short description of what the sub-agent does",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "System prompt defining the sub-agent's persona, behavior, "
                    "and instructions. Must include the required output format."
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of tools to restrict the sub-agent to "
                    "(e.g. ['read', 'glob', 'grep']). If omitted, all tools are available."
                ),
            },
            "color": {
                "type": "string",
                "description": "Hex color for display (default: #ff00ff)",
            },
        },
        required=["name", "description", "prompt"],
        handler=_,
    )
