from __future__ import annotations

from core.providers.base import AIProvider
from core.providers.manager import ProviderManager


class AIRouter:
    def __init__(self, settings):
        self.settings = settings
        self.provider_manager = ProviderManager(settings)

    @property
    def active_name(self) -> str:
        return self.provider_manager.active_name

    async def get_provider(self, name: str | None = None) -> AIProvider:
        return await self.provider_manager.get_provider(name)

    async def set_active(self, name: str) -> None:
        await self.provider_manager.set_active(name)
        self.settings.provider = name

    async def reconfigure(self, name: str, **kwargs) -> None:
        # Update settings
        prefix = f"{name}_"
        for k, v in kwargs.items():
            if v is not None:
                attr = f"{prefix}{k}"
                if hasattr(self.settings, attr):
                    setattr(self.settings, attr, v)
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
        prefix = f"{self.active_name}_"
        return getattr(self.settings, f"{prefix}model")

    async def list_providers(self) -> list[dict]:
        result = []
        for name in ["nvidia", "local", "openai", "anthropic"]:
            prefix = f"{name}_"
            key = getattr(self.settings, f"{prefix}api_key")
            key_preview = key[:8] + "…" if key else "(none)"
            result.append({
                "name": name,
                "type": name,
                "model": getattr(self.settings, f"{prefix}model"),
                "key": key_preview,
                "url": getattr(self.settings, f"{prefix}base_url"),
                "active": name == self.active_name,
            })
        return result

    async def list_models(self, name: str | None = None) -> list[str]:
        target = name or self.active_name
        return await self.provider_manager.list_models(target)

    async def test_connection(self, name: str) -> tuple[bool, str]:
        return await self.provider_manager.test_connection(name)

    async def provider_info(self, name: str) -> str:
        prefix = f"{name}_"
        return f"{name}/{getattr(self.settings, f'{prefix}model')}"

    async def switch_model(self, name: str | None = None, model: str | None = None) -> None:
        await self.provider_manager.switch_model(name, model)