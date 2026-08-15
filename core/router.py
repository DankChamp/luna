from __future__ import annotations

from core.providers.base import AIProvider
from core.providers.manager import ProviderManager
from core.config_manager import ConfigManager, ProviderDef


class AIRouter:
    def __init__(self, config: ConfigManager):
        self.config = config
        self.provider_manager = ProviderManager(config)

    @property
    def active_name(self) -> str:
        return self.provider_manager._active_provider or ""

    async def get_provider(self, name: str | None = None) -> AIProvider:
        return await self.provider_manager.get_provider(name)

    async def set_active(self, name: str) -> None:
        await self.provider_manager.set_active(name)

    async def reconfigure(self, name: str, **kwargs) -> None:
        self.config.update_provider(name, **kwargs)
        # Rebuild provider
        if name in self.provider_manager._providers:
            del self.provider_manager._providers[name]
        await self.provider_manager.get_provider(name)

    async def cached_models(self, name: str | None = None) -> list[str]:
        return await self.provider_manager.list_models(name)

    def cached_models_sync(self, name: str | None = None) -> list[str]:
        provider = self.provider_manager._providers.get(name or self.active_name)
        if provider and hasattr(provider, '_model_cache') and provider._model_cache:
            return provider._model_cache
        return []

    def provider_names_sync(self) -> list[str]:
        return self.provider_manager.get_provider_names()

    async def cycle_model(self) -> str:
        models = await self.cached_models()
        if not models:
            return ""
        # Get current index from provider
        provider = self.provider_manager._providers.get(self.active_name)
        current_index = getattr(provider, '_model_index', 0)
        current_index = (current_index + 1) % len(models)
        if provider:
            provider._model_index = current_index
        model = models[current_index]
        await self.reconfigure(self.active_name, model=model)
        return model

    @property
    def active_model(self) -> str:
        p = self.config.get_provider(self.active_name)
        return p.model if p else ""

    async def list_providers(self) -> list[dict]:
        return self.config.list_providers_info()

    async def list_models(self, name: str) -> list[str]:
        return await self.provider_manager.list_models(name)

    async def test_connection(self, name: str) -> tuple[bool, str]:
        return await self.provider_manager.test_connection(name)

    async def provider_info(self, name: str) -> str:
        p = self.config.get_provider(name)
        if not p:
            return "Unknown"
        return f"{p.type}/{p.model}"

    async def switch_model(self, name: str | None = None, model: str | None = None) -> None:
        await self.provider_manager.switch_model(name, model)