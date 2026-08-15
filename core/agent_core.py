from __future__ import annotations
from typing import AsyncIterator, Callable, Awaitable
from dataclasses import dataclass

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
from session.context import trim_to_budget
from tools.registry import ToolRegistry


@dataclass
class AgentConfig:
    """Configuration for AgentCore."""
    max_iterations: int = 15
    max_history_tokens: int = 100
    emma_context: str = ""
    mode: AgentMode = AgentMode.BUILD
    active_skills: list[str] = None
    system_prompt: str = ""
    mode_extra: str = ""
    
    def __post_init__(self):
        if self.active_skills is None:
            self.active_skills = []


class AgentCore:
    """
    Core message loop and tool execution engine.
    
    Handles:
    - Provider communication (streaming completions)
    - Tool call execution via ToolExecutor
    - Message history management
    - Token budgeting and history trimming
    - Iteration control
    
    Does NOT handle:
    - Persona/skill/subagent loading (delegated to Agent)
    - Project config, permissions, references, memory (delegated to Agent)
    - Mode switching, provider switching (delegated to Agent)
    """
    
    def __init__(
        self,
        router: AIRouter,
        tools: ToolRegistry,
        config: AgentConfig,
        provider_getter: Callable[[], Awaitable[AIProvider]],
        get_provider_name: Callable[[], str],
    ):
        self.router = router
        self.tools = tools
        self.config = config
        self._provider_getter = provider_getter
        self._get_provider_name = get_provider_name
        self.messages: list[dict] = []
    
    async def run(self, user_input: str) -> AsyncIterator[StreamEvent | str]:
        """
        Main message loop.
        
        Yields:
            TextChunk - streaming text from provider
            ToolExecStart - tool execution started
            ToolExecEnd - tool execution completed
            str - final response text
        """
        self.messages.append({"role": "user", "content": user_input})
        
        provider = await self._provider_getter()
        max_iterations = self.config.max_iterations
        final_text = ""
        
        for iteration in range(max_iterations):
            collected_text = ""
            tool_calls = None
            
            messages = self._build_messages()
            
            try:
                async for event in provider.complete(messages, self.tools.definitions):
                    if isinstance(event, TextChunk):
                        collected_text += event.text
                        # Check if this is a provider error (e.g., malformed JSON from model)
                        if collected_text.strip().startswith("[Local Error") or collected_text.strip().startswith("[Error"):
                            # Don't yield the error, just stop
                            final_text = collected_text
                            self.messages.append({"role": "assistant", "content": collected_text})
                            break
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
            
            # Convert tool calls to message format
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
            
            # Execute tool calls via ToolExecutor
            for tc in tool_calls:
                yield ToolExecStart(name=tc.name, arguments=tc.arguments)
                result = await self.tools.execute(tc)
                yield ToolExecEnd(name=tc.name, result=result)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            
            # If tool executed successfully and returned a result, we can continue
            # The next iteration will let the model respond to the tool result
            if iteration == max_iterations - 1:
                final_text = "Reached maximum iteration limit."
                yield TextChunk(text=final_text)
                self.messages.append({"role": "assistant", "content": final_text})
                break
        
        if final_text:
            yield final_text
    
    def _build_messages(self) -> list[dict]:
        """Build message list for provider with system prompt and history."""
        prompt_parts = [self.config.system_prompt]
        
        if self.config.mode_extra:
            prompt_parts.append(self.config.mode_extra)
        
        if self.config.active_skills:
            # Skills are added by Agent, not here
            pass
        
        system = {"role": "system", "content": "\n\n".join(prompt_parts)}
        
        history = self.messages[-self.config.max_history_tokens * 2:]
        
        trimmed = trim_to_budget(
            [system] + history,
            model=self._get_provider_name(),
        )
        
        return trimmed
    
    def set_system_prompt(self, prompt: str):
        """Update the base system prompt."""
        self.config.system_prompt = prompt

    async def set_provider(self, provider: str | None = None, model: str | None = None):
        """Switch active provider and/or model."""
        if provider:
            await self.router.set_active(provider)
        if model:
            await self.router.switch_model(None, model)
    
    def set_emma_context(self, context: str):
        """Set Emma's delegation context."""
        self.config.emma_context = context
    
    def set_mode(self, mode: AgentMode):
        """Update agent mode and blocked tools."""
        self.config.mode = mode
        self.tools.set_blocked(MODE_TOOL_BLOCKS.get(mode, set()))
    
    def reset(self):
        """Clear conversation history."""
        self.messages = []
    
    def load_messages(self, messages: list[dict]):
        """Load conversation history."""
        self.messages = messages
    
    def undo_last(self) -> list[str]:
        return self.tools.undo_last()
    
    def redo_last(self) -> list[str]:
        return self.tools.redo_last()