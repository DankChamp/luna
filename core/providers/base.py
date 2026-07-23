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


StreamEvent = TextChunk | ToolCallBatch


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
