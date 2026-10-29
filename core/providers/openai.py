from __future__ import annotations

import json

from core.providers.base import (
    StreamEvent,
    TextChunk,
    ToolCall,
    ToolCallBatch,
)
from core.providers.base_provider import BaseProvider
from core.providers.config import ProviderConfig


class OpenAIProvider(BaseProvider):
    """OpenAI provider."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url or None,
            )
        return self._client

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        stream = await self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            tools=tools,
            stream=True,
        )

        tool_calls_accum: dict[int, dict] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield TextChunk(text=delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_accum:
                        tool_calls_accum[idx] = {
                            "id": tc.id,
                            "name": "",
                            "arguments": "",
                        }
                    if tc.function.name:
                        tool_calls_accum[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_accum[idx]["arguments"] += tc.function.arguments

        if tool_calls_accum:
            calls = []
            for idx in sorted(tool_calls_accum.keys()):
                tc = tool_calls_accum[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))
            yield ToolCallBatch(calls=calls)

    async def list_models(self) -> list[str]:
        try:
            models = await self.client.models.list()
            return [m.id for m in models.data]
        except Exception:
            return [
                "gpt-4o",
                "gpt-4o-mini",
                "gpt-4-turbo",
                "gpt-4",
                "gpt-3.5-turbo",
            ]

    async def test_connection(self) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            return True, f"OK — {len(models)} models available"
        except Exception as e:
            return False, str(e)
