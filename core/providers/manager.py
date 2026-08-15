from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Protocol
from dataclasses import dataclass
from typing import Optional

import json
import httpx

from core.providers.base import (
    AIProvider,
    StreamEvent,
    TextChunk,
    ToolCall,
    ToolCallBatch,
    ToolExecStart,
    ToolExecEnd,
)
from core.errors import ProviderError
from core.config_manager import ConfigManager, ProviderDef


class ProviderProtocol(Protocol):
    """Protocol for pluggable providers."""

    @property
    def name(self) -> str:
        ...

    @property
    def default_model(self) -> str:
        ...

    @property
    def base_url(self) -> str:
        ...

    @property
    def api_key(self) -> str:
        ...

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ...

    async def list_models(self) -> list[str]:
        ...

    async def switch_model(self, model: str) -> None:
        ...

    async def test_connection(self) -> tuple[bool, str]:
        ...


class BaseProvider(ABC):
    """Base class for AI providers."""

    def __init__(self, config: ProviderDef):
        self.config = config

    @property
    def name(self) -> str:
        return f"{self.config.type}/{self.config.model}"

    @property
    def default_model(self) -> str:
        return self.config.model

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def api_key(self) -> str:
        return self.config.api_key

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _build_body(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        body: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        return body

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        ...

    async def switch_model(self, model: str) -> None:
        """Switch to a different model."""
        self.config.model = model

    async def test_connection(self) -> tuple[bool, str]:
        """Test provider connectivity."""
        try:
            models = await self.list_models()
            if models:
                return True, f"Connected. {len(models)} models available."
            return False, "No models available"
        except Exception as e:
            return False, str(e)


class NvidiaNIMProvider(BaseProvider):
    """NVIDIA NIM provider with model switching via /models endpoint."""

    def __init__(self, config: ProviderDef):
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


class LocalProvider(BaseProvider):
    """Local Ollama-compatible provider."""

    def __init__(self, config: ProviderDef):
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
            "messages": messages,
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

    def _parse_ollama_stream(self, line: str) -> Optional[dict]:
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
                    
                    # For Ollama with tools, use non-streaming
                    if self._is_ollama and tools:
                        body = self._build_ollama_body(messages, tools)
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
                        
                        # Parse tool calls from content if needed
                        if content and not tool_calls:
                            import re
                            if content.strip().startswith('{"name"') or (content.strip().startswith('{\n') and '"name"' in content[:50]):
                                try:
                                    parsed = json.loads(content.strip())
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
                                
                                # Map hallucinated tool names
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
                    
                    # Streaming path
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
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                yield TextChunk(text="[Local Error: Connection failed]")
                return

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


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider."""

    def __init__(self, config: ProviderDef):
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
                if chunk.delta.stop_reason == "tool_use":
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
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]

    async def test_connection(self) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            return True, f"OK — {len(models)} models available"
        except Exception as e:
            return False, str(e)


class OpenAIProvider(BaseProvider):
    """OpenAI provider."""

    def __init__(self, config: ProviderDef):
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


class ProviderRegistry:
    """Registry for pluggable providers."""

    _providers: dict[str, type[BaseProvider]] = {
        "nvidia": NvidiaNIMProvider,
        "local": LocalProvider,
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
    }

    @classmethod
    def register(cls, name: str, provider_class: type[BaseProvider]) -> None:
        cls._providers[name] = provider_class

    @classmethod
    def get(cls, name: str) -> type[BaseProvider] | None:
        return cls._providers.get(name)

    @classmethod
    def create(cls, provider_type: str, config: ProviderDef) -> BaseProvider:
        provider_class = cls._providers.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown provider type: {provider_type}")
        return provider_class(config)

    @classmethod
    def list_types(cls) -> list[str]:
        return list(cls._providers.keys())


class ProviderManager:
    """Manages provider instances and model switching."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self._providers: dict[str, BaseProvider] = {}
        self._active_provider: str | None = None

    def _create_provider(self, name: str, provider_def: ProviderDef) -> BaseProvider:
        return ProviderManager.create_provider(provider_def.type, provider_def)

    @staticmethod
    def create_provider(provider_type: str, provider_def: ProviderDef) -> BaseProvider:
        return ProviderRegistry.create(provider_type, provider_def)

    async def get_provider(self, name: str | None = None) -> BaseProvider:
        target = name or self.config_manager.active_provider
        if target not in self._providers:
            provider_def = self.config_manager.get_provider(target)
            if not provider_def:
                raise ValueError(f"Provider not found: {target}")
            self._providers[target] = self.create_provider(target, provider_def)
        self._active_provider = target
        return self._providers[target]

    async def get_active_provider(self) -> BaseProvider:
        if self._active_provider:
            return self._providers[self._active_provider]
        return await self.get_provider()

    async def set_active(self, name: str) -> None:
        if name in self.config_manager.provider_names():
            self._active_provider = name

    async def switch_model(self, name: str | None = None, model: str | None = None) -> None:
        target = name or self._active_provider
        if target not in self._providers:
            await self.get_provider(target)
        provider = self._providers[target]
        await provider.switch_model(model)

    async def list_models(self, name: str | None = None) -> list[str]:
        target = name or self._active_provider
        provider = await self.get_provider(target)
        return await provider.list_models()

    async def test_connection(self, name: str | None = None) -> tuple[bool, str]:
        target = name or self._active_provider
        provider = await self.get_provider(target)
        return await provider.test_connection()

    def get_provider_names(self) -> list[str]:
        return list(self._providers.keys())