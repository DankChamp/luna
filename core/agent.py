from __future__ import annotations
from typing import AsyncIterator

from core.personality import LUNA_SYSTEM_PROMPT
from core.providers.base import AIProvider, StreamEvent, TextChunk, ToolCallBatch
from core.router import AIRouter
from tools import create_default_registry


class Agent:
    def __init__(self, settings, router: AIRouter):
        self.settings = settings
        self.router = router
        self.tools = create_default_registry()
        self.messages: list[dict] = []
        self._provider: AIProvider | None = None

    async def set_provider(self, name: str | None = None):
        self._provider = await self.router.get_provider(force_provider=name)

    async def provider(self) -> AIProvider:
        if self._provider is None:
            self._provider = await self.router.get_provider()
        return self._provider

    async def provider_name(self) -> str:
        p = await self.provider()
        return p.name

    async def run(self, user_input: str) -> AsyncIterator[StreamEvent | str]:
        self.messages.append({"role": "user", "content": user_input})

        provider = await self.provider()
        max_iterations = 15
        final_text = ""

        for iteration in range(max_iterations):
            collected_text = ""
            tool_calls = None

            async for event in provider.complete(
                self._build_messages(),
                self.tools.definitions,
            ):
                if isinstance(event, TextChunk):
                    collected_text += event.text
                    yield event
                elif isinstance(event, ToolCallBatch):
                    tool_calls = event.calls

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
                result = await self.tools.execute(tc)
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
        system = {"role": "system", "content": LUNA_SYSTEM_PROMPT}
        history = self.messages[-self.settings.luna_max_history * 2:]
        return [system] + history

    def reset(self):
        self.messages = []

    def load_messages(self, messages: list[dict]):
        self.messages = messages
