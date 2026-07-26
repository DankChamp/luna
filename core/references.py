from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Reference:
    alias: str
    path: str | None = None
    repository: str | None = None
    branch: str | None = None
    description: str = ""
    hidden: bool = False
    resolved_path: Path | None = None


class ReferenceManager:
    def __init__(self):
        self._refs: dict[str, Reference] = {}
        self._config_path: Path | None = None
        self._base_dir: Path | None = None

    def load_from_config(self, cfg: dict | None):
        if not cfg:
            return
        for alias, raw in cfg.items():
            if isinstance(raw, str):
                p = Path(raw)
                if self._base_dir and not p.is_absolute():
                    p = self._base_dir / p
                self._refs[alias] = Reference(
                    alias=alias,
                    path=raw,
                    resolved_path=p.expanduser().resolve() if not raw.startswith(("http://", "https://", "git@", "github:")) else None,
                    description="",
                )
            elif isinstance(raw, dict):
                path = raw.get("path")
                repository = raw.get("repository")
                branch = raw.get("branch")
                resolved = None
                if path:
                    p = Path(path)
                    if self._base_dir and not p.is_absolute():
                        p = self._base_dir / p
                    resolved = p.expanduser().resolve()
                self._refs[alias] = Reference(
                    alias=alias,
                    path=path,
                    repository=repository,
                    branch=branch,
                    description=raw.get("description", ""),
                    hidden=raw.get("hidden", False),
                    resolved_path=resolved,
                )

    def discover(self, start: Path | None = None) -> Path | None:
        cwd = start or Path.cwd()
        current = cwd.resolve()
        for _ in range(10):
            candidate = current / ".luna" / "config.json"
            if candidate.exists():
                self._config_path = candidate
                self._base_dir = candidate.parent.parent
                try:
                    raw = json.loads(candidate.read_text(encoding="utf-8"))
                    refs_raw = raw.get("references")
                    if refs_raw:
                        self.load_from_config(refs_raw)
                except Exception:
                    pass
                return candidate
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None

    def get(self, alias: str) -> Reference | None:
        return self._refs.get(alias)

    def list_refs(self) -> list[Reference]:
        return list(self._refs.values())

    def attach_description(self) -> str:
        visible = [r for r in self._refs.values() if r.description and not r.hidden]
        if not visible:
            return ""
        lines = ["Available reference directories:"]
        for r in visible:
            loc = r.resolved_path or r.path or r.repository or ""
            lines.append(f"  @{r.alias}: {r.description} ({loc})")
        return "\n".join(lines)
