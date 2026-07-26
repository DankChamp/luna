from __future__ import annotations

from .providers.base import AIProvider
from .providers.nvidia_nim import NvidiaNIMProvider
from .providers.local import LocalProvider
from .config_manager import ConfigManager, ProviderDef


PROVIDER_BUILDERS = {
    "nvidia": lambda d: NvidiaNIMProvider(api_key=d.api_key, base_url=d.base_url, model=d.model),
    "local": lambda d: LocalProvider(base_url=d.base_url, api_key=d.api_key, model=d.model),
}


class AIRouter:
    def __init__(self, config: ConfigManager):
        self.config = config
        self._providers: dict[str, AIProvider] = {}
        self._active: str | None = None
        self._sync()

    def _sync(self):
        for name, defn in self.config.get_all_providers().items():
            if name not in self._providers:
                self._providers[name] = self._build(defn)
        self._active = self.config.active

    def _build(self, defn: ProviderDef) -> AIProvider:
        builder = PROVIDER_BUILDERS.get(defn.type)
        if not builder:
            raise ValueError(f"Unknown provider type: {defn.type}")
        return builder(defn)

    def _rebuild(self, name: str):
        defn = self.config.get_provider(name)
        if defn:
            self._providers[name] = self._build(defn)

    @property
    def active_name(self) -> str:
        return self._active or ""

    async def get_provider(self, name: str | None = None) -> AIProvider:
        target = name or self._active
        if target not in self._providers:
            self._sync()
        result = self._providers.get(target)
        if result:
            return result
        result = self._providers.get("nvidia")
        if result:
            return result
        providers = list(self._providers.values())
        if providers:
            return providers[0]
        raise RuntimeError("No providers configured. Check your .env or config.json.")

    async def set_active(self, name: str):
        if name in self._providers:
            self.config.active = name
            self._active = name

    async def reconfigure(self, name: str, **kwargs):
        self.config.update_provider(name, **kwargs)
        self._rebuild(name)

    async def list_providers(self) -> list[dict]:
        return self.config.list_providers_info()

    async def list_models(self, name: str) -> list[str]:
        provider = await self.get_provider(name)
        if hasattr(provider, "list_models"):
            try:
                return await provider.list_models()
            except NotImplementedError:
                pass
        return []

    async def test_connection(self, name: str) -> tuple[bool, str]:
        provider = await self.get_provider(name)
        return await provider.test_connection()

    async def provider_info(self, name: str) -> str:
        p = self.config.get_provider(name)
        if not p:
            return "Unknown"
        return f"{p.type}/{p.model}"
