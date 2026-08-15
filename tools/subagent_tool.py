from __future__ import annotations
from typing import Any, Optional
from dataclasses import dataclass, field

from tools.registry import ToolDef, ToolRegistry
from core.subagents import SubagentManager
from core.router import AIRouter
from core.general_subagent import GeneralSubagentManager, TaskDelegation
from core.errors import SubagentError, ToolError

import anyio


@dataclass
class SubagentInvocation:
    """Represents a subagent invocation from the main agent."""
    name: str
    prompt: str
    model: str | None = None
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] = field(default_factory=lambda: ["todowrite"])
    system_prompt: str | None = None


def create_task_tool(
    subagent_manager: SubagentManager,
    router: AIRouter,
    parent_tools: Any,
    model_overrides: dict[str, str] | None = None,
) -> ToolDef:
    """Create the task tool for delegating to subagents."""
    
    general_manager = GeneralSubagentManager(router, parent_tools)

    async def task(
        prompt: str,
        agent: str = "general",
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        blocked_tools: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """
        Delegate a task to a subagent.
        
        Args:
            prompt: The task description/prompt for the subagent
            agent: Subagent name (default: "general")
            model: Optional model override
            allowed_tools: List of allowed tool names
            blocked_tools: List of blocked tool names
            system_prompt: Custom system prompt for the subagent
            
        Returns:
            The subagent's response
        """
        try:
            # Check if it's a known subagent
            if agent != "general" and subagent_manager.get(agent):
                subagent = subagent_manager.get(agent)
                provider = await router.get_provider(model)
                
                # Get tools for this subagent
                tool_defs = subagent.get_tools(allowed_tools)
                
                # Build messages
                system_prompt_text = subagent.build_system_prompt()
                if system_prompt:
                    system_prompt_text += f"\n\n{system_prompt}"
                
                messages = [
                    {"role": "system", "content": system_prompt_text},
                    {"role": "user", "content": prompt},
                ]
                
                full_text = ""
                async for event in router.get_provider().complete(messages, tool_defs):
                    if hasattr(event, 'text') and event.text:
                        full_text += event.text
                
                return full_text
            
            # General subagent
            delegation = TaskDelegation(
                name=agent,
                prompt=prompt,
                model=model,
                allowed_tools=allowed_tools,
                blocked_tools=blocked_tools or ["todowrite"],
                system_prompt=system_prompt,
            )
            
            result = await general_manager.run_delegation(delegation)
            
            if not result.success:
                raise SubagentError(f"Subagent '{agent}' failed: {result.error}", agent)
            
            return result.output
            
        except Exception as e:
            if isinstance(e, SubagentError):
                raise
            raise ToolError(f"Task delegation failed: {e}", "task")

    return ToolDef(
        name="task",
        description=(
            "Delegate a task to a subagent for parallel execution. "
            "Use for complex research, multi-file operations, or independent tasks. "
            "Subagents run in isolated contexts with their own tool access."
        ),
        parameters={
            "prompt": {
                "type": "string",
                "description": "The task description or question for the subagent",
            },
            "agent": {
                "type": "string",
                "description": "Subagent name (default: 'general'). Use 'general' for research tasks.",
                "default": "general",
            },
            "model": {
                "type": "string",
                "description": "Optional model override (e.g., 'nvidia/llama-3.1-8b-instruct')",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tool names to allow for this subagent",
            },
            "blocked_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tool names to block for this subagent",
            },
            "system_prompt": {
                "type": "string",
                "description": "Custom system prompt for the subagent",
            },
        },
        required=["prompt"],
        handler=task,
    )


def create_subagent_tool(
    subagent_manager: SubagentManager,
    router: AIRouter,
) -> ToolDef:
    """Create tool for listing and managing subagents."""

    async def list_subagents() -> str:
        """List all available subagents."""
        agents = subagent_manager.list_subagents()
        if not agents:
            return "No subagents available."
        
        result = ["Available subagents:"]
        for name in agents:
            subagent = subagent_manager.get(name)
            if subagent:
                desc = getattr(subagent, 'description', 'No description')
                result.append(f"  @{name}: {desc}")
        return "\n".join(result)

    async def run_subagent(
        name: str,
        prompt: str,
        model: str | None = None,
    ) -> str:
        """Run a specific subagent."""
        subagent = subagent_manager.get(name)
        if not subagent:
            return f"Subagent '{name}' not found"
        
        provider = await router.get_provider(model)
        tool_defs = subagent.get_tools()
        
        system_prompt = subagent.build_system_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        
        full_text = ""
        async for event in provider.complete(messages, tool_defs):
            if hasattr(event, 'text') and event.text:
                full_text += event.text
        
        return full_text

    return ToolDef(
        name="subagent",
        description="List or run subagents for specialized tasks",
        parameters={
            "action": {
                "type": "string",
                "description": "Action to perform",
                "enum": ["list", "run"],
            },
            "name": {
                "type": "string",
                "description": "Subagent name (for 'run' action)",
            },
            "prompt": {
                "type": "string",
                "description": "Task prompt for the subagent (for 'run' action)",
            },
            "model": {
                "type": "string",
                "description": "Optional model override",
            },
        },
        required=["action"],
        handler=lambda action, **kwargs: (
            list_subagents() if action == "list" 
            else run_subagent(name=kwargs.get("name", ""), prompt=kwargs.get("prompt", ""), model=kwargs.get("model"))
        ),
    )