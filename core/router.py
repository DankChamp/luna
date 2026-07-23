from .providers.base import AIProvider
from .providers.nvidia_nim import NvidiaNIMProvider
from .providers.local import LocalProvider


class AIRouter:
    def __init__(self, settings):
        self.local = LocalProvider.from_settings(settings)
        self.nvidia = NvidiaNIMProvider.from_settings(settings)
        self._current_provider: AIProvider | None = None

    @property
    def current_provider(self) -> AIProvider | None:
        return self._current_provider

    async def get_provider(self, force_provider: str | None = None) -> AIProvider:
        if force_provider == "local":
            self._current_provider = self.local
            return self.local
        if force_provider == "nvidia":
            self._current_provider = self.nvidia
            return self.nvidia

        if await self.local.is_available():
            self._current_provider = self.local
            return self.local

        self._current_provider = self.nvidia
        return self.nvidia

    async def list_providers(self) -> list[str]:
        available = []
        if await self.local.is_available():
            available.append(f"local ({self.local.default_model})")
        if await self.nvidia.is_available():
            available.append(f"nvidia ({self.nvidia.default_model})")
        return available
