from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_nested_delimiter="__",
    )

    # Active provider
    provider: Literal["nvidia", "local", "openai", "anthropic"] = "local"

    # Provider configs (flat, using env_nested_delimiter)
    # NVIDIA
    nvidia_type: str = "nvidia"
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.1-8b-instruct"

    # Local (Ollama)
    local_type: str = "local"
    local_api_key: str = ""
    local_base_url: str = "http://127.0.0.1:11434"
    local_model: str = "qwen2.5-coder:7b"

    # OpenAI
    openai_type: str = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # Anthropic
    anthropic_type: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # Agent settings
    max_history_tokens: int = 8000
    max_iterations: int = 15
    temperature: float = 0.2

    # Sessions (XDG)
    session_dir: str = "~/.local/share/luna/sessions"

    # Bridge
    bridge_token: str = ""
    bridge_port: int = 8701

    # UI
    theme: str = "neon"
    minimal_ui: bool = False

    def get_provider_config(self, name: str | None = None) -> dict:
        """Get provider config as dict."""
        target = name or self.provider
        prefix = f"{target}_"
        return {
            "type": getattr(self, f"{prefix}type"),
            "api_key": getattr(self, f"{prefix}api_key"),
            "base_url": getattr(self, f"{prefix}base_url"),
            "model": getattr(self, f"{prefix}model"),
        }