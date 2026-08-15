from __future__ import annotations
import json
from typing import AsyncIterator

import httpx

from .base import AIProvider, StreamEvent, TextChunk, ToolCall, ToolCallBatch


class LocalProvider(AIProvider):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        # Detect if using Ollama native API (base_url without /v1)
        self._is_ollama = "/v1" not in self.base_url and not self.base_url.endswith("/v1")
        self.api_key = api_key
        self.default_model = model

    @property
    def name(self) -> str:
        return f"local/{self.default_model}"

    def _get_models_url(self) -> str:
        if self._is_ollama:
            return f"{self.base_url}/api/tags"
        return f"{self.base_url}/models"

    def _get_chat_url(self) -> str:
        if self._is_ollama:
            return f"{self.base_url}/api/chat"
        return f"{self.base_url}/chat/completions"

    def _build_ollama_body(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {
            "model": self.default_model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            # Convert OpenAI tool format to Ollama format (no "type": "function" wrapper)
            ollama_tools = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t["function"]
                    ollama_tools.append({
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
            body["tools"] = ollama_tools
        return body

    def _parse_ollama_stream(self, line: str):
        """Parse Ollama's streaming response format."""
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            return None
        
        message = chunk.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])
        
        return {
            "content": content,
            "tool_calls": tool_calls,
            "done": chunk.get("done", False),
        }

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self._get_models_url())
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if self.api_key and not self._is_ollama:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                resp = await client.get(self._get_models_url(), headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if self._is_ollama:
                        return [m["name"] for m in data.get("models", [])]
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
                    resp = await client.get(self._get_models_url())
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
        if self.api_key and not self._is_ollama:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        if self._is_ollama:
            body = self._build_ollama_body(messages, tools)
        else:
            body = self._build_body(messages, tools)
        
        max_retries = 2

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    url = self._get_chat_url()
                    
                    # For Ollama with tools, use non-streaming (streaming doesn't support tool_calls properly)
                    if self._is_ollama and tools:
                        body["stream"] = False
                        resp = await client.post(url, json=body, headers=headers)
                        if resp.status_code != 200:
                            error_text = resp.text
                            yield TextChunk(text=f"[Local Error {resp.status_code}] {error_text}")
                            return
                        
                        data = resp.json()
                        message = data.get("message", {})
                        content = message.get("content", "")
                        tool_calls = message.get("tool_calls", [])
                        
                        # Some models (like qwen2.5-coder) return tool calls in content as JSON
                        # Parse it if it looks like a tool call
                        if content and not tool_calls:
                            stripped = content.strip()
                            # Handle markdown code blocks (```json, ```bash, ```)
                            if stripped.startswith('```'):
                                # Extract content between ``` and ```
                                import re
                                match = re.search(r'```(?:\w+)?\s*(.*?)\s*```', stripped, re.DOTALL)
                                if match:
                                    stripped = match.group(1).strip()
                            # Match any JSON starting with {"name" (allowing for various whitespace)
                            if stripped.startswith('{"name"') or (stripped.startswith('{\n') and '"name"' in stripped[:50]):
                                try:
                                    parsed = json.loads(stripped)
                                    if "name" in parsed and "arguments" in parsed:
                                        tool_calls = [{"function": parsed}]
                                        content = ""
                                except json.JSONDecodeError:
                                    pass
                        
                        if content:
                            yield TextChunk(text=content)
                        
                        if tool_calls:
                            for tc in tool_calls:
                                fn = tc.get("function", {})
                                tool_name = fn.get("name", "")
                                tool_args = fn.get("arguments", {})
                                
                                # Map common hallucinated tool names to actual tools
                                if tool_name in ("create_folder", "mkdir", "make_directory"):
                                    tool_name = "bash"
                                    tool_args = {"command": f"mkdir -p {tool_args.get('folder_name', tool_args.get('directory_name', tool_args.get('path', '')))}"}
                                elif tool_name in ("run_command", "execute", "shell"):
                                    tool_name = "bash"
                                    if "command" not in tool_args:
                                        tool_args = {"command": tool_args.get("cmd", tool_args.get("command", ""))}
                                
                                yield ToolCallBatch(calls=[ToolCall(
                                    id=tc.get("id", f"call_{len(tool_calls)}"),
                                    name=tool_name,
                                    arguments=tool_args,
                                )])
                        return
                    
                    # Streaming path (no tools, or non-Ollama)
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            error_text = await resp.aread()
                            error_str = error_text.decode('utf-8', errors='replace')
                            if attempt < max_retries - 1 and resp.status_code in (429, 500, 502, 503):
                                import asyncio
                                await asyncio.sleep(2 ** attempt)
                                continue
                            
                            # Try to extract tool call from Ollama 400 error (model outputting malformed tool call)
                            if resp.status_code == 400 and "Value looks like object" in error_str:
                                import re
                                # Extract JSON-like content from error
                                match = re.search(r'\{[^}]*"name"[^}]*"arguments"[^}]*\}', error_str)
                                if match:
                                    try:
                                        parsed = json.loads(match.group())
                                        if "name" in parsed and "arguments" in parsed:
                                            yield ToolCallBatch(calls=[ToolCall(
                                                id=f"call_{len(error_str)}",
                                                name=parsed["name"],
                                                arguments=parsed["arguments"],
                                            )])
                                            return
                                    except json.JSONDecodeError:
                                        pass
                            
                            yield TextChunk(text=f"[Local Error {resp.status_code}] {error_str}")
                            return

                        tool_calls_accum: dict[int, dict] = {}
                        
                        if self._is_ollama:
                            # Ollama native streaming format
                            async for line in resp.aiter_lines():
                                if not line.strip():
                                    continue
                                parsed = self._parse_ollama_stream(line)
                                if not parsed:
                                    continue
                                
                                if parsed["content"]:
                                    yield TextChunk(text=parsed["content"])
                                
                                if parsed["tool_calls"]:
                                    for tc in parsed["tool_calls"]:
                                        fn = tc.get("function", {})
                                        yield ToolCallBatch(calls=[ToolCall(
                                            id=tc.get("id", f"call_{len(tool_calls_accum)}"),
                                            name=fn.get("name", ""),
                                            arguments=fn.get("arguments", {}),
                                        )])
                                
                                if parsed["done"]:
                                    return
                        else:
                            # OpenAI-compatible streaming format
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
