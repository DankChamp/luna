from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class TextChunk:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolCallBatch:
    calls: list[ToolCall]


@dataclass
class ToolExecStart:
    name: str
    arguments: dict


@dataclass
class ToolExecEnd:
    name: str
    result: str


StreamEvent = TextChunk | ToolCallBatch | ToolExecStart | ToolExecEnd


@dataclass
class CompletionUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class CompletionResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: CompletionUsage | None = None


class AIProvider(ABC):
    default_model: str
    base_url: str
    api_key: str

    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    async def list_models(self) -> list[str]:
        raise NotImplementedError

    async def test_connection(self) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            if models:
                return True, f"Connected. {len(models)} models available."
            return True, "Connected."
        except NotImplementedError:
            try:
                available = await self.is_available()
                if available:
                    return True, "Available."
                return False, "Not available."
            except Exception as e:
                return False, str(e)
        except Exception as e:
            return False, str(e)

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    def _build_body(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict:
        body: dict = {
            "model": self.default_model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        return body
