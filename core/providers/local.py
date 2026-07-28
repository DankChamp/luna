from __future__ import annotations
import json
from typing import AsyncIterator

import httpx

from .base import AIProvider, StreamEvent, TextChunk, ToolCall, ToolCallBatch


class LocalProvider(AIProvider):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = model

    @property
    def name(self) -> str:
        return f"local/{self.default_model}"

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                resp = await client.get(f"{self.base_url}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return [m["id"] for m in data.get("data", [])]
                return []
        except Exception:
            return []

    async def test_connection(self) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            if models:
                return True, f"OK — {len(models)} models available"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.base_url}/models")
                    if resp.status_code == 200:
                        return True, "Connected"
                    return False, f"HTTP {resp.status_code}"
            except Exception as e:
                return False, str(e)
        except Exception as e:
            return False, str(e)

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        headers = self._build_headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = self._build_body(messages, tools)
        max_retries = 2

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    url = f"{self.base_url}/chat/completions"
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            error_text = await resp.aread()
                            if attempt < max_retries - 1 and resp.status_code in (429, 500, 502, 503):
                                import asyncio
                                await asyncio.sleep(2 ** attempt)
                                continue
                            yield TextChunk(text=f"[Local Error {resp.status_code}] {error_text.decode('utf-8', errors='replace')}")
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
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                yield TextChunk(text="[Local Error: Connection failed]")
                return
