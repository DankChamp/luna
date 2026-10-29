from __future__ import annotations

import json

import httpx

from core.providers.base import (
    StreamEvent,
    TextChunk,
    ToolCall,
    ToolCallBatch,
)
from core.providers.base_provider import BaseProvider
from core.providers.config import ProviderConfig


class NvidiaNIMProvider(BaseProvider):
    """NVIDIA NIM provider with model switching via /models endpoint."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._model_cache: list[str] | None = None

    async def list_models(self) -> list[str]:
        """List available models from NVIDIA NIM."""
        if self._model_cache is not None:
            return self._model_cache

        if not self.config.api_key:
            return []

        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.config.base_url}/models",
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._model_cache = [m["id"] for m in data.get("data", [])]
                    return self._model_cache
                return []
        except Exception:
            return []

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        headers = self._build_headers()
        body = self._build_body(messages, tools)
        max_retries = 3

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    url = f"{self.config.base_url}/chat/completions"
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            error_text = await resp.aread()
                            if attempt < max_retries - 1 and resp.status_code in (429, 500, 502, 503):
                                import asyncio
                                await asyncio.sleep(2 ** attempt)
                                continue
                            yield TextChunk(
                                text=f"[Error {resp.status_code}] {error_text.decode('utf-8', errors='replace')}"
                            )
                            return

                        tool_calls_accum: dict[int, dict] = {}

                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break

                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            choices = chunk.get("choices", [])
                            if not choices:
                                continue

                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield TextChunk(text=content)

                            tc_deltas = delta.get("tool_calls")
                            if tc_deltas:
                                for tc in tc_deltas:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_calls_accum:
                                        tool_calls_accum[idx] = {
                                            "id": tc.get("id", f"call_{idx}"),
                                            "name": "",
                                            "arguments": "",
                                        }
                                    func = tc.get("function", {})
                                    if "name" in func and func["name"]:
                                        tool_calls_accum[idx]["name"] = func["name"]
                                    if "arguments" in func and func["arguments"]:
                                        tool_calls_accum[idx]["arguments"] += func["arguments"]

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
                        return
            except httpx.TimeoutException:
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                yield TextChunk(text="[Error: Request timed out after 3 retries]")
                return
            except httpx.NetworkError as e:
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                hint = ""
                err = str(e)
                if "[Errno -2]" in err or "Name or service not known" in err:
                    hint = (
                        f" — host in base_url '{self.config.base_url}' does not resolve. "
                        "Check the provider's base_url in config."
                    )
                yield TextChunk(text=f"[Network Error: {e}{hint}]")
                return

    async def test_connection(self) -> tuple[bool, str]:
        """Test NVIDIA NIM connection."""
        if not self.config.api_key:
            return False, "No API key configured"
        models = await self.list_models()
        if models:
            preview = models[:5]
            more = f" and {len(models) - 5} more" if len(models) > 5 else ""
            return True, f"OK — {len(models)} models available ({', '.join(preview)}{more})"
        return False, "No models available"
