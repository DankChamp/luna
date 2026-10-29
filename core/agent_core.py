from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from core.logging import get_logger
from core.modes import MODE_TOOL_BLOCKS, AgentMode
from core.providers.base import (
    AIProvider,
    StreamEvent,
    TextChunk,
    ToolCallBatch,
    ToolExecEnd,
    ToolExecStart,
)
from core.router import AIRouter
from session.context import trim_to_budget
from tools.registry import ToolRegistry

logger = get_logger("luna.agent_core")


@dataclass
class AgentConfig:
    """Configuration for AgentCore."""
    max_iterations: int = 15
    max_history_tokens: int = 100
    emma_context: str = ""
    mode: AgentMode = AgentMode.BUILD
    active_skills: list[str] = field(default_factory=list)
    system_prompt: str = ""
    mode_extra: str = ""
    # Provider error handling
    max_provider_retries: int = 3
    retryable_errors: tuple = ("timeout", "network", "503", "429", "502", "500")


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
        # Circuit breaker state
        self._consecutive_errors = 0
        self._circuit_open = False
        self._circuit_open_time = 0.0

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if an error is retryable."""
        error_str = str(error).lower()
        return any(err in error_str for err in self.config.retryable_errors)

    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker allows request."""
        if not self._circuit_open:
            return True
        # Try to close circuit after 30 seconds
        if time.time() - self._circuit_open_time > 30:
            logger.info("Circuit breaker: attempting to close circuit")
            self._circuit_open = False
            self._consecutive_errors = 0
            return True
        return False

    def _record_error(self):
        """Record an error for circuit breaker."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= 5:
            self._circuit_open = True
            self._circuit_open_time = time.time()
            logger.warning("Circuit breaker opened after %d consecutive errors", self._consecutive_errors)

    def _record_success(self):
        """Record a successful request."""
        self._consecutive_errors = 0

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
            # Check circuit breaker
            if not self._check_circuit_breaker():
                yield TextChunk(text="[Error: Circuit breaker open - too many consecutive failures. Try switching providers.]")
                return

            collected_text = ""
            tool_calls = None

            messages = self._build_messages()

            # Retry loop for provider errors
            for attempt in range(self.config.max_provider_retries):
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
                    # Success - break retry loop
                    self._record_success()
                    break
                except Exception as e:
                    self._record_error()
                    if attempt == self.config.max_provider_retries - 1:
                        # Last attempt - yield error and stop
                        error_msg = f"[Error after {self.config.max_provider_retries} retries: {e}]"
                        logger.error("Provider error after retries", error=str(e), attempts=self.config.max_provider_retries)
                        yield TextChunk(text=error_msg)
                        final_text = error_msg
                        break
                    elif self._is_retryable_error(e):
                        # Retryable error - wait and retry
                        wait_time = 2 ** attempt
                        logger.warning("Provider error (retryable), retrying in %ds: %s", wait_time, e)
                        yield TextChunk(text=f"[Retrying in {wait_time}s...]")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # Non-retryable error - fail immediately
                        error_msg = f"[Error: {e}]"
                        logger.error("Provider error (non-retryable)", error=str(e))
                        yield TextChunk(text=error_msg)
                        final_text = error_msg
                        break
            else:
                # All retries exhausted
                final_text = "[Error: Max retries exceeded]"
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
