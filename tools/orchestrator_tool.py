from __future__ import annotations

from core.orchestrator import Orchestrator
from .registry import ToolDef


def create_orchestrator_tool(orch: Orchestrator) -> ToolDef:
    async def orchestrate(plan: str) -> str:
        results = await orch.execute(plan)
        return orch.status_report()

    return ToolDef(
        name="orchestrate",
        description="Execute a multi-step plan by distributing tasks across subagents.\n"
        "Format the plan as:\n"
        "### task_name\n"
        "- agent: subagent_name\n"
        "- depends: other_task_id (optional, comma-separated)\n"
        "- prompt: description of what to do",
        parameters={
            "plan": {
                "type": "string",
                "description": "Structured plan with tasks, agents, dependencies, and prompts",
            },
        },
        required=["plan"],
        handler=orchestrate,
    )
