from __future__ import annotations

import json
import re

import httpx

from core.providers.base import (
    StreamEvent,
    TextChunk,
    ToolCall,
    ToolCallBatch,
)
from core.providers.base_provider import BaseProvider
from core.providers.config import ProviderConfig


class OllamaProvider(BaseProvider):
    """Ollama-compatible local provider."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # Detect if using Ollama native API
        self._is_ollama = "/v1" not in self.config.base_url

    def _get_models_url(self) -> str:
        if self._is_ollama:
            return f"{self.config.base_url}/api/tags"
        return f"{self.config.base_url}/models"

    def _get_chat_url(self) -> str:
        if self._is_ollama:
            return f"{self.config.base_url}/api/chat"
        return f"{self.config.base_url}/chat/completions"

    def _build_ollama_body(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict:
        body = {
            "model": self.config.model,
            "messages": self._convert_ollama_messages(messages),
            "stream": True,
        }
        if tools:
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

    def _convert_ollama_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-style assistant tool_calls to Ollama's native format."""
        converted = []
        for msg in messages:
            out = dict(msg)
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                ollama_calls = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", tc)
                    name = fn.get("name", "")
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    ollama_calls.append({"function": {"name": name, "arguments": args}})
                out["tool_calls"] = ollama_calls
            if msg.get("role") == "tool":
                # Ollama tool messages carry the result in `content`
                out.pop("tool_call_id", None)
            converted.append(out)
        return converted

    def _parse_ollama_stream(self, line: str) -> dict | None:
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

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if self.config.api_key and not self._is_ollama:
                    headers["Authorization"] = f"Bearer {self.config.api_key}"
                resp = await client.get(self._get_models_url(), headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if self._is_ollama:
                        return [m["name"] for m in data.get("models", [])]
                    return [m["id"] for m in data.get("data", [])]
                return []
        except Exception:
            return []

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        headers = self._build_headers()

        if self._is_ollama:
            body = self._build_ollama_body(messages, tools)
        else:
            body = self._build_body(messages, tools)

        max_retries = 2

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    url = self._get_chat_url()

                    # Always stream, even with tools. Some models (qwen2.5-coder) emit
                    # tool calls as JSON text in `content`; we detect that below.
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            error_text = await resp.aread()
                            error_str = error_text.decode('utf-8', errors='replace')
                            if attempt < max_retries - 1 and resp.status_code in (429, 500, 502, 503):
                                import asyncio
                                await asyncio.sleep(2 ** attempt)
                                continue

                            # Ollama fails to parse the model's tool-call JSON
                            # ("Value looks like object, but can't find closing '}' symbol").
                            # Extract the tool call from the error, or retry without tools.
                            if resp.status_code == 400 and "Value looks like object" in error_str:
                                match = re.search(r'\{[^}]*"name"[^}]*"arguments"[^}]*\}', error_str)
                                if match:
                                    try:
                                        parsed = json.loads(match.group())
                                        if "name" in parsed and "arguments" in parsed:
                                            yield ToolCallBatch(calls=[self._make_tool_call(parsed)])
                                            return
                                    except json.JSONDecodeError:
                                        pass
                                # Fall back: retry once without tools so the model can
                                # respond as plain text instead of a malformed tool call.
                                if attempt < max_retries - 1 and self._is_ollama:
                                    body = self._build_ollama_body(messages, None)
                                    continue

                            yield TextChunk(text=f"[Local Error {resp.status_code}] {error_str}")
                            return

                        if self._is_ollama:
                            # Ollama native streaming format
                            pending = ""
                            async for line in resp.aiter_lines():
                                if not line.strip():
                                    continue
                                parsed = self._parse_ollama_stream(line)
                                if not parsed:
                                    continue

                                if parsed["tool_calls"]:
                                    # Structured tool calls (llama3-style templates)
                                    if pending:
                                        yield TextChunk(text=pending)
                                        pending = ""
                                    for tc in parsed["tool_calls"]:
                                        fn = tc.get("function", {})
                                        yield ToolCallBatch(calls=[ToolCall(
                                            id=tc.get("id", f"call_{len(parsed['tool_calls'])}"),
                                            name=fn.get("name", ""),
                                            arguments=fn.get("arguments", {}),
                                        )])
                                    continue

                                content = parsed.get("content", "")
                                if content:
                                    # Some models emit tool calls as JSON text in content.
                                    # Buffer the content and detect a complete tool-call object.
                                    pending += content
                                    stripped = pending.lstrip()
                                    if stripped.startswith("{"):
                                        try:
                                            obj = json.loads(pending)
                                            if isinstance(obj, dict) and "name" in obj and (
                                                "arguments" in obj or "parameters" in obj
                                            ):
                                                yield ToolCallBatch(calls=[self._make_tool_call(obj)])
                                                pending = ""
                                                continue
                                        except json.JSONDecodeError:
                                            if len(pending) > 6000 and not pending.rstrip().endswith("}"):
                                                yield TextChunk(text=pending)
                                                pending = ""
                                            continue
                                        if pending.rstrip().endswith("}"):
                                            yield TextChunk(text=pending)
                                            pending = ""
                                            continue
                                    elif self._looks_like_fenced_json(pending):
                                        # Markdown-fenced JSON (```json ... ```) — try to extract
                                        obj = self._try_parse_fenced_tool_call(pending)
                                        if obj is not None:
                                            yield ToolCallBatch(calls=[self._make_tool_call(obj)])
                                            pending = ""
                                            continue
                                        import re
                                        # If the content after the fence opener isn't JSON,
                                        # it's prose — stream it rather than buffer forever.
                                        rest = re.sub(r'^(`{3}|~{3})[a-zA-Z0-9._-]*\n?', '', pending.lstrip()).lstrip()
                                        if rest and not rest.startswith("{"):
                                            yield TextChunk(text=pending)
                                            pending = ""
                                            continue
                                        if len(pending) > 8000:
                                            yield TextChunk(text=pending)
                                            pending = ""
                                            continue
                                    else:
                                        # Plain text — pending is always empty here
                                        yield TextChunk(text=content)

                                if parsed["done"]:
                                    if pending and "{" in pending:
                                        yield TextChunk(text=pending)
                                    return
                        else:
                            # OpenAI-compatible streaming format
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

    def _try_parse_fenced_tool_call(self, text: str) -> dict | None:
        """Try to extract a tool-call JSON object from markdown-fenced content."""
        candidate = text.lstrip()
        # Strip opening fence (```json, ```, ~~~) — newline after the tag is optional
        m = re.match(r'^(`{3}|~{3})[a-zA-Z0-9._-]*\n?', candidate)
        if m:
            candidate = candidate[m.end():].lstrip()
        # Strip closing fence
        stripped = candidate.rstrip()
        if stripped.endswith("```") or stripped.endswith("~~~"):
            stripped = stripped[:-3].rstrip()
        if not stripped.startswith("{"):
            return None
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict) and "name" in obj and (
            "arguments" in obj or "parameters" in obj
        ):
            return obj
        return None

    def _looks_like_fenced_json(self, text: str) -> bool:
        """True if text starts with a code fence (possibly mid-buffer)."""
        stripped = text.lstrip()
        return stripped.startswith("```") or stripped.startswith("~~~")

    def _make_tool_call(self, obj: dict) -> ToolCall:
        """Build a ToolCall from a JSON object, mapping common hallucinated names."""
        tool_name = obj.get("name", "")
        tool_args = obj.get("arguments", obj.get("parameters", {}))

        if tool_name in ("create_folder", "mkdir", "make_directory"):
            tool_name = "bash"
            tool_args = {"command": f"mkdir -p {tool_args.get('folder_name', tool_args.get('directory_name', tool_args.get('path', '')))}"}
        elif tool_name in ("run_command", "execute", "shell"):
            tool_name = "bash"
            if "command" not in tool_args:
                tool_args = {"command": tool_args.get("cmd", tool_args.get("command", ""))}
        elif tool_name in ("write_file", "writeFile", "create_file", "file_write"):
            tool_name = "write"
            # Map filename/file_path/file_name/file -> path for compatibility
            if "filename" in tool_args and "path" not in tool_args:
                tool_args["path"] = tool_args.pop("filename")
            if "file_path" in tool_args and "path" not in tool_args:
                tool_args["path"] = tool_args.pop("file_path")
            if "file_name" in tool_args and "path" not in tool_args:
                tool_args["path"] = tool_args.pop("file_name")
            if "file" in tool_args and "path" not in tool_args:
                tool_args["path"] = tool_args.pop("file")

        return ToolCall(
            id=obj.get("id", f"call_{abs(hash(str(obj)))}"),
            name=tool_name,
            arguments=tool_args,
        )

    async def test_connection(self) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            if models:
                return True, f"OK — {len(models)} models available"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self._get_models_url())
                if resp.status_code == 200:
                    return True, "Connected"
                return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)
