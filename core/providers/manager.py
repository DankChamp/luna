from __future__ import annotations

from core.providers.base_provider import BaseProvider
from core.providers.config import ProviderConfig


class ProviderRegistry:
    """Registry for pluggable providers."""

    _providers: dict[str, type[BaseProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[BaseProvider]) -> None:
        cls._providers[name] = provider_class

    @classmethod
    def get(cls, name: str) -> type[BaseProvider] | None:
        return cls._providers.get(name)

    @classmethod
    def create(cls, provider_type: str, config: ProviderConfig) -> BaseProvider:
        provider_class = cls._providers.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown provider type: {provider_type}")
        return provider_class(config)

    @classmethod
    def list_types(cls) -> list[str]:
        return list(cls._providers.keys())


# Register built-in providers
def _register_builtin():
    from core.providers.anthropic import AnthropicProvider
    from core.providers.nvidia import NvidiaNIMProvider
    from core.providers.ollama import OllamaProvider
    from core.providers.openai import OpenAIProvider

    ProviderRegistry.register("nvidia", NvidiaNIMProvider)
    ProviderRegistry.register("local", OllamaProvider)
    ProviderRegistry.register("anthropic", AnthropicProvider)
    ProviderRegistry.register("openai", OpenAIProvider)


_register_builtin()


class ProviderManager:
    """Manages provider instances and model switching using Settings."""

    def __init__(self, settings):
        self.settings = settings
        self._providers: dict[str, BaseProvider] = {}
        self._active_provider: str | None = None

    def _get_provider_config(self, name: str) -> ProviderConfig:
        """Get provider config from settings."""
        prefix = f"{name}_"
        return ProviderConfig(
            type=name,
            api_key=getattr(self.settings, f"{prefix}api_key"),
            base_url=getattr(self.settings, f"{prefix}base_url"),
            model=getattr(self.settings, f"{prefix}model"),
        )

    async def get_provider(self, name: str | None = None) -> BaseProvider:
        target = name or self.settings.provider
        if target not in self._providers:
            config = self._get_provider_config(target)
            self._providers[target] = ProviderRegistry.create(target, config)
        self._active_provider = target
        return self._providers[target]

    async def get_active_provider(self) -> BaseProvider:
        if self._active_provider:
            return self._providers[self._active_provider]
        return await self.get_provider()

    async def set_active(self, name: str) -> None:
        if hasattr(self.settings, name):
            self.settings.provider = name
            self._active_provider = name

    async def switch_model(self, name: str | None = None, model: str | None = None) -> None:
        target = name or self._active_provider or self.settings.provider
        if target not in self._providers:
            await self.get_provider(target)
        provider = self._providers[target]
        await provider.switch_model(model)
        # Update settings
        setattr(self.settings, f"{target}_model", model)

    async def list_models(self, name: str | None = None) -> list[str]:
        target = name or self._active_provider or self.settings.provider
        provider = await self.get_provider(target)
        return await provider.list_models()

    async def test_connection(self, name: str | None = None) -> tuple[bool, str]:
        target = name or self._active_provider or self.settings.provider
        provider = await self.get_provider(target)
        return await provider.test_connection()

    def get_provider_names(self) -> list[str]:
        return list(self._providers.keys())

    @property
    def active_name(self) -> str:
        return self._active_provider or self.settings.provider
