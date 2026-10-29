from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Protocol

from core.providers.base import (
    StreamEvent,
)
from core.providers.config import ProviderConfig


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

    def __init__(self, config: ProviderConfig):
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
