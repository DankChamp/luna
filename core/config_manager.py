from __future__ import annotations
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProviderDef:
    type: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    label: str = ""


class ConfigManager:
    def __init__(self, env_settings):
        self.env = env_settings
        self.config_path = Path("~/.luna/config.json").expanduser()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text())
            except Exception:
                pass
        return self._seed_from_env()

    def _seed_from_env(self) -> dict:
        data = {
            "active": "nvidia",
            "providers": {
                "nvidia": {
                    "type": "nvidia",
                    "api_key": self.env.nvidia_nim_api_key,
                    "base_url": self.env.nvidia_nim_base_url,
                    "model": self.env.nvidia_nim_default_model,
                },
                "local": {
                    "type": "local",
                    "api_key": self.env.local_api_key,
                    "base_url": self.env.local_base_url,
                    "model": self.env.local_default_model,
                },
            },
        }
        self._write(data)
        return data

    def _write(self, data: dict):
        self.config_path.write_text(json.dumps(data, indent=2))
        try:
            # Holds API keys in plaintext — keep it readable only by the owner.
            os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def save(self):
        self._write(self.data)

    @property
    def active(self) -> str:
        return self.data.get("active", list(self.data.get("providers", {}))[0] or "nvidia")

    @active.setter
    def active(self, name: str):
        if name in self.data.get("providers", {}):
            self.data["active"] = name
            self.save()

    def get_provider(self, name: str) -> ProviderDef | None:
        p = self.data.get("providers", {}).get(name)
        if not p:
            return None
        return ProviderDef(**p)

    def get_all_providers(self) -> dict[str, ProviderDef]:
        raw = self.data.get("providers", {})
        return {name: ProviderDef(**p) for name, p in raw.items()}

    def update_provider(self, name: str, **kwargs):
        providers = self.data.setdefault("providers", {})
        if name not in providers:
            return
        for k, v in kwargs.items():
            if v is not None:
                providers[name][k] = v
        self.save()

    def provider_names(self) -> list[str]:
        return list(self.data.get("providers", {}).keys())

    def list_providers_info(self) -> list[dict]:
        result = []
        for name, p in self.data.get("providers", {}).items():
            key_preview = p["api_key"][:8] + "…" if p.get("api_key") else "(none)"
            result.append({
                "name": name,
                "type": p.get("type", ""),
                "model": p.get("model", ""),
                "key": key_preview,
                "url": p.get("base_url", ""),
                "active": name == self.active,
            })
        return result

    def set_key(self, name: str, key: str):
        self.update_provider(name, api_key=key)

    def set_model(self, name: str, model: str):
        self.update_provider(name, model=model)

    def set_url(self, name: str, url: str):
        self.update_provider(name, base_url=url)
