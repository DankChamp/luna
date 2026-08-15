from __future__ import annotations
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field
from typing import AsyncIterator

from core.subagents import SubagentManager
from core.providers.base import AIProvider, TextChunk
from core.router import AIRouter
from tools import create_default_registry
from core.permissions import PermissionEvaluator
from core.concurrency import run_parallel, StructuredConcurrency
from core.errors import SubagentError, ToolError

import anyio


@dataclass
class SubagentResult:
    """Result of subagent execution."""
    success: bool
    output: str
    error: str | None = None
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class GeneralSubagentConfig:
    """Configuration for general subagent."""
    name: str = "general"
    description: str = "General-purpose subagent for research and multi-step tasks"
    model: str | None = None
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] = field(default_factory=lambda: ["todowrite"])
    max_iterations: int = 10
    max_history_tokens: int = 50
    system_prompt: str = ""


class GeneralSubagent:
    """General-purpose subagent that can run tasks in parallel."""

    def __init__(
        self,
        config: GeneralSubagentConfig,
        router: AIRouter,
        parent_tools: Any = None,
    ):
        self.config = config
        self.router = router
        self.parent_tools = parent_tools
        self._tools = None

    def _get_tools(self) -> Any:
        """Get tool registry for this subagent."""
        if self._tools is None:
            if self.parent_tools:
                tools = self.parent_tools
            else:
                tools = create_default_registry()
            # Block configured tools
            if self.config.blocked_tools:
                tools.set_blocked(set(self.config.blocked_tools))
            self._tools = tools
        return self._tools

    def build_system_prompt(self) -> str:
        """Build system prompt for this subagent."""
        base_prompt = self.config.system_prompt or """You are a general-purpose research and task execution agent.
You excel at:
- Exploring codebases and finding relevant information
- Running commands and analyzing output
- Reading and understanding files
- Synthesizing information from multiple sources
- Breaking down complex tasks into manageable steps

Guidelines:
- Be thorough and precise
- Use tools to gather evidence before concluding
- Cite sources (file paths, command output) when making claims
- Return clear, structured results
- Do not modify files unless explicitly asked"""
        return base_prompt

    async def run(
        self,
        prompt: str,
        provider: AIProvider | None = None,
        tools_subset: list[str] | None = None,
    ) -> SubagentResult:
        """Run the subagent on a task."""
        if provider is None:
            provider = await self.router.get_provider()

        tools = self._get_tools()
        if tools_subset:
            # Filter tools if subset specified
            pass

        messages = [
            {"role": "system", "content": self.build_system_prompt()},
            {"role": "user", "content": prompt},
        ]

        tool_defs = self._get_tools().definitions

        try:
            full_text = ""
            tool_calls_made = []

            async for event in provider.complete(messages, tool_defs):
                if hasattr(event, 'text') and event.text:
                    full_text += event.text
                elif hasattr(event, 'calls'):
                    for tc in event.calls:
                        tool_calls_made.append({
                            "name": tc.name,
                            "arguments": tc.arguments,
                        })

            return SubagentResult(
                success=True,
                output=full_text,
                tool_calls=tool_calls_made,
            )

        except Exception as e:
            return SubagentResult(
                success=False,
                output="",
                error=f"Subagent failed: {e}",
            )


class ParallelSubagentRunner:
    """Runs multiple subagents in parallel using anyio."""

    def __init__(self, router: AIRouter, parent_tools: Any = None):
        self.router = router
        self.parent_tools = parent_tools
        self._subagents: dict[str, GeneralSubagent] = {}

    def create_subagent(
        self,
        name: str,
        prompt: str,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        blocked_tools: list[str] | None = None,
        system_prompt: str | None = None,
        max_iterations: int = 10,
    ) -> GeneralSubagent:
        """Create a configured subagent."""
        config = GeneralSubagentConfig(
            name=name,
            model=model,
            allowed_tools=allowed_tools,
            blocked_tools=blocked_tools or ["todowrite"],
            max_iterations=max_iterations,
            system_prompt=system_prompt or "",
        )
        subagent = GeneralSubagent(config, self.router, self.parent_tools)
        self._subagents[name] = subagent
        return subagent

    async def run_parallel(
        self,
        tasks: list[dict[str, Any]],
        max_concurrency: int = 3,
    ) -> list[SubagentResult]:
        """Run multiple subagent tasks in parallel."""
        async def run_task(task: dict[str, Any]) -> SubagentResult:
            name = task.get("name", "task")
            prompt = task.get("prompt", "")
            model = task.get("model")
            allowed = task.get("allowed_tools")
            blocked = task.get("blocked_tools", ["todowrite"])
            system = task.get("system_prompt")

            subagent = self.create_subagent(
                name=name,
                prompt=prompt,
                model=model,
                allowed_tools=allowed,
                blocked_tools=blocked,
                system_prompt=system,
            )

            provider = await self.router.get_provider(model) if model else await self.router.get_provider()
            return await subagent.run(prompt, provider)

        return await run_parallel(
            [lambda t=task: run_task(t) for task in tasks],
            max_concurrency=max_concurrency,
        )

    async def run_sequential(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[SubagentResult]:
        """Run subagent tasks sequentially."""
        results = []
        for task in tasks:
            result = await self.run_parallel([task], max_concurrency=1)
            results.append(result[0])
        return results

    async def run_general(
        self,
        prompt: str,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> SubagentResult:
        """Run the general subagent on a single task."""
        subagent = self.create_subagent(
            name="general",
            prompt=prompt,
            model=model,
            allowed_tools=allowed_tools,
            blocked_tools=["todowrite"],
        )
        provider = await self.router.get_provider(model)
        return await subagent.run(prompt, provider)


@dataclass
class TaskDelegation:
    """Represents a delegated task for the general subagent."""
    name: str
    prompt: str
    model: str | None = None
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] = field(default_factory=lambda: ["todowrite"])
    system_prompt: str | None = None
    priority: int = 0  # Higher = run first


class GeneralSubagentManager:
    """High-level manager for general subagent delegation."""

    def __init__(self, router: AIRouter, parent_tools: Any = None):
        self.runner = ParallelSubagentRunner(router, parent_tools)
        self._task_history: list[TaskDelegation] = []

    def delegate(
        self,
        name: str,
        prompt: str,
        **kwargs: Any,
    ) -> TaskDelegation:
        """Create a task delegation."""
        task = TaskDelegation(name=name, prompt=prompt, **kwargs)
        self._task_history.append(task)
        return task

    async def run_delegation(
        self,
        task: TaskDelegation,
    ) -> SubagentResult:
        """Execute a single delegation."""
        subagent = self.runner.create_subagent(
            name=task.name,
            prompt=task.prompt,
            model=task.model,
            allowed_tools=task.allowed_tools,
            blocked_tools=task.blocked_tools,
            system_prompt=task.system_prompt,
        )
        provider = await self.router.get_provider(task.model)
        return await subagent.run(task.prompt, provider)

    async def run_parallel(
        self,
        tasks: list[TaskDelegation],
        max_concurrency: int = 3,
    ) -> list[SubagentResult]:
        """Run multiple delegations in parallel."""
        return await self.runner.run_parallel(
            [
                {
                    "name": t.name,
                    "prompt": t.prompt,
                    "model": t.model,
                    "allowed_tools": t.allowed_tools,
                    "blocked_tools": t.blocked_tools,
                    "system_prompt": t.system_prompt,
                }
                for t in tasks
            ],
            max_concurrency=max_concurrency,
        )

    def get_history(self) -> list[TaskDelegation]:
        return self._task_history.copy()

    def clear_history(self) -> None:
        self._task_history.clear()