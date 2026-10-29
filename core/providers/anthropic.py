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


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self.config.api_key)
        return self._client

    async def list_models(self) -> list[str]:
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # Convert messages to Anthropic format
        system_prompt = ""
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg.get("content", "")
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                })

        # Convert tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t["function"]
                    anthropic_tools.append({
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {}),
                    })

        stream = await self.client.messages.create(
            model=self.config.model,
            max_tokens=8192,
            system=system_prompt or None,
            messages=anthropic_messages,
            tools=anthropic_tools,
            stream=True,
        )

        tool_calls_accum: dict[int, dict] = {}

        async for chunk in stream:
            if chunk.type == "content_block_delta":
                if chunk.delta.type == "text":
                    yield TextChunk(text=chunk.delta.text)
                elif chunk.delta.type == "input_json_delta":
                    idx = chunk.index
                    if idx not in tool_calls_accum:
                        tool_calls_accum[idx] = {
                            "id": f"call_{idx}",
                            "name": "",
                            "arguments": "",
                        }
                    tool_calls_accum[idx]["arguments"] += chunk.delta.partial_json
            elif chunk.type == "content_block_start":
                if chunk.content_block.type == "tool_use":
                    idx = chunk.index
                    tool_calls_accum[idx] = {
                        "id": chunk.content_block.id,
                        "name": chunk.content_block.name,
                        "arguments": "",
                    }
            elif chunk.type == "message_delta":
                if chunk.delta.stop_reason == "tool_use" and tool_calls_accum:
                    calls = []
                    for idx in sorted(tool_calls_accum.keys()):
                        tc = tool_calls_accum[idx]
                        try:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))
                    yield ToolCallBatch(calls=calls)

    async def test_connection(self) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            return True, f"OK — {len(models)} models available"
        except Exception as e:
            return False, str(e)
