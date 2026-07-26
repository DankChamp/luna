from __future__ import annotations

from .registry import ToolDef


def create_task_handler(subagent_manager, model_overrides=None):
    async def handle(agent: str, prompt: str) -> str:
        return await subagent_manager.run(agent, prompt, model_overrides=model_overrides)

    return handle


def create_task_tool(subagent_manager, model_overrides=None) -> ToolDef:
    handler = create_task_handler(subagent_manager, model_overrides)
    return ToolDef(
        name="task",
        description="Launch a specialized sub-agent for a specific task.\n"
        f"{subagent_manager.task_description()}",
        parameters={
            "agent": {
                "type": "string",
                "description": "Name of the sub-agent to use. Available: "
                + ", ".join(a.name for a in subagent_manager.list_subagents() if not a.hidden),
            },
            "prompt": {
                "type": "string",
                "description": "Detailed task description for the sub-agent",
            },
        },
        required=["agent", "prompt"],
        handler=handler,
    )
