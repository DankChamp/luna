from __future__ import annotations
from pathlib import Path
from typing import AsyncIterator

from core.persona import PersonaLoader
from core.providers.base import (
    AIProvider,
    StreamEvent,
    TextChunk,
    ToolCallBatch,
    ToolExecStart,
    ToolExecEnd,
)
from core.router import AIRouter
from core.modes import AgentMode, MODE_TOOL_BLOCKS, MODE_PROMPTS
from core.skills import SkillManager
from core.subagents import SubagentManager
from core.mcp import MCPManager
from core.permissions import PermissionEvaluator
from core.project_config import ProjectConfig
from session.context import trim_to_budget
from tools import create_default_registry
from tools.task import create_task_tool


class Agent:
    def __init__(self, settings, router: AIRouter):
        self.settings = settings
        self.router = router
        self.tools = create_default_registry()
        self.messages: list[dict] = []
        self._provider: AIProvider | None = None
        self.mode: AgentMode = AgentMode.BUILD

        self.persona: PersonaLoader | None = None
        self.skills: SkillManager | None = None
        self.active_skills: list[str] = []
        self.subagents: SubagentManager | None = None
        self.mcp: MCPManager | None = None
        self.project_config: ProjectConfig | None = None
        self.permissions: PermissionEvaluator = PermissionEvaluator()
        self.references: object = None
        self.lsp: object = None
        self.memory: object = None
        self.watcher: object = None
        self.orch: object = None
        self._emma_context: str = ""
        self._model_override: str | None = None

    def set_skill_manager(self, mgr: SkillManager):
        self.skills = mgr

    def set_subagent_manager(self, mgr: SubagentManager):
        self.subagents = mgr
        model_overrides = self.project_config.agent_models if self.project_config else None
        task_tool = create_task_tool(mgr, model_overrides=model_overrides)
        self.tools.register(task_tool)

    def set_persona(self, persona: PersonaLoader):
        self.persona = persona

    def set_project_config(self, cfg: ProjectConfig | None):
        self.project_config = cfg
        if cfg and cfg.permissions:
            self.permissions = PermissionEvaluator(cfg.permissions)
            self.tools.set_permissions(self.permissions)

    def set_mode(self, mode: AgentMode):
        self.mode = mode
        self.tools.set_blocked(MODE_TOOL_BLOCKS.get(mode, set()))

    def toggle_mode(self) -> AgentMode:
        new = AgentMode.PLAN if self.mode == AgentMode.BUILD else AgentMode.BUILD
        self.set_mode(new)
        return self.mode

    async def set_provider(self, name: str | None = None, model: str | None = None):
        if name:
            await self.router.set_active(name)
        if model:
            self._model_override = model
        self._provider = await self.router.get_provider(name)

    async def provider(self) -> AIProvider:
        self._provider = await self.router.get_provider()
        return self._provider

    @property
    def provider_name(self) -> str:
        return self.router.active_name

    def model_for_agent(self, name: str) -> str | None:
        if self._model_override:
            return self._model_override
        if self.project_config:
            return self.project_config.model_for_agent(name)
        return None

    async def run(self, user_input: str) -> AsyncIterator[StreamEvent | str]:
        self.messages.append({"role": "user", "content": user_input})

        provider = await self.provider()
        max_iterations = 15
        final_text = ""

        for iteration in range(max_iterations):
            collected_text = ""
            tool_calls = None

            messages = self._build_messages()

            try:
                async for event in provider.complete(messages, self.tools.definitions):
                    if isinstance(event, TextChunk):
                        collected_text += event.text
                        yield event
                    elif isinstance(event, ToolCallBatch):
                        tool_calls = event.calls
            except Exception as e:
                error_msg = f"[Error: {e}]"
                yield TextChunk(text=error_msg)
                final_text = error_msg
                break

            if tool_calls is None:
                self.messages.append({"role": "assistant", "content": collected_text})
                final_text = collected_text
                break

            tc_list = []
            for tc in tool_calls:
                tc_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": str(tc.arguments)},
                })
            self.messages.append({
                "role": "assistant",
                "content": collected_text,
                "tool_calls": tc_list,
            })

            for tc in tool_calls:
                yield ToolExecStart(name=tc.name, arguments=tc.arguments)
                result = await self.tools.execute(tc)
                yield ToolExecEnd(name=tc.name, result=result)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            if iteration == max_iterations - 1:
                final_text = "Reached maximum iteration limit."
                yield TextChunk(text=final_text)
                self.messages.append({"role": "assistant", "content": final_text})
                break

        if final_text:
            yield final_text

    def _build_messages(self) -> list[dict]:
        prompt = self._build_system_prompt()

        mode_extra = MODE_PROMPTS.get(self.mode, "")
        if mode_extra:
            prompt += mode_extra

        if self.active_skills and self.skills:
            for name in self.active_skills:
                skill = self.skills.get(name)
                if skill:
                    prompt += f"\n\n## Loaded Skill: {skill.name}\n{skill.instructions}"

        if self.subagents:
            prompt += f"\n\n## Available Subagents\n{self.subagents.task_description()}"

        system = {"role": "system", "content": prompt}

        max_tokens = getattr(self.settings, "luna_max_history", 100)
        history = self.messages[-max_tokens * 2:]

        trimmed = trim_to_budget(
            [system] + history,
            model=self.provider_name,
        )

        return trimmed

    def _build_system_prompt(self) -> str:
        if self.persona:
            prompt = self.persona.build_system_prompt()
        else:
            from core.personality import LUNA_SYSTEM_PROMPT
            prompt = LUNA_SYSTEM_PROMPT

        if self._emma_context:
            prompt += f"\n\n## Emma's context\n{self._emma_context}"

        agents_md = self._load_agents_md()
        if agents_md:
            prompt += f"\n\n## Project Rules\n{agents_md}"

        refs_desc = self._load_references_description()
        if refs_desc:
            prompt += f"\n\n{refs_desc}"

        memory_summary = self._load_memory_summary()
        if memory_summary:
            prompt += f"\n\n{memory_summary}"

        return prompt

    def _load_agents_md(self) -> str:
        candidates = [
            Path.cwd() / "AGENTS.md",
            Path.cwd() / "CLAUDE.md",
            Path.home() / ".luna" / "AGENTS.md",
        ]
        for p in candidates:
            if p.exists() and p.is_file():
                try:
                    return p.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        return ""

    def _load_references_description(self) -> str:
        if not self.references:
            return ""
        return self.references.attach_description()

    def _load_memory_summary(self) -> str:
        if not self.memory:
            return ""
        summary = self.memory.summarize()
        return summary if summary else ""

    def reset(self):
        self.messages = []

    def load_messages(self, messages: list[dict]):
        self.messages = messages

    def undo_last(self) -> list[str]:
        return self.tools.undo_last()

    def redo_last(self) -> list[str]:
        return self.tools.redo_last()
