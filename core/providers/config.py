from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderConfig:
    """Provider configuration."""
    type: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    label: str = ""
