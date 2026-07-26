from __future__ import annotations
from pathlib import Path
from typing import Optional


class PersonaLoader:
    def __init__(self, *search_dirs: str | Path):
        self.search_dirs = [Path(d).expanduser().resolve() for d in search_dirs]
        self._cached_prompt: str | None = None
        self._dirty = True

    def _resolve(self, *parts: str) -> Path | None:
        for d in self.search_dirs:
            p = d.joinpath(*parts)
            if p.exists() and p.is_file():
                return p
        return None

    def _resolve_dir(self, *parts: str) -> Path | None:
        for d in self.search_dirs:
            p = d.joinpath(*parts)
            if p.exists() and p.is_dir():
                return p
        return None

    def _read(self, *parts: str) -> str:
        p = self._resolve(*parts)
        if p is None:
            return ""
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def reload(self):
        self._dirty = True

    def _load_rules(self) -> str:
        rules_dir = self._resolve_dir("rules")
        if rules_dir is None:
            return ""
        parts: list[str] = []
        for f in sorted(rules_dir.iterdir()):
            if f.suffix == ".md" and f.is_file():
                try:
                    text = f.read_text(encoding="utf-8").strip()
                    if text:
                        name = f.stem.replace("_", " ").replace("-", " ").title()
                        parts.append(f"### {name}\n{text}")
                except Exception:
                    pass
        return "\n\n".join(parts)

    def build_system_prompt(self) -> str:
        if self._cached_prompt is not None and not self._dirty:
            return self._cached_prompt
        self._dirty = False

        core = self._read("core.md")
        workflow = self._read("workflow.md")
        user = self._read("user.md")
        rules = self._load_rules()

        sections: list[str] = []
        if core:
            sections.append(core)
        if workflow:
            sections.append(f"## Workflow\n{workflow}")
        if rules:
            sections.append(f"## Project Rules\n{rules}")
        if user:
            sections.append(f"## About the User\n{user}")

        self._cached_prompt = "\n\n".join(sections)
        return self._cached_prompt

    def status(self) -> dict:
        loaded = []
        missing = []
        for name in ("core.md", "workflow.md", "user.md"):
            p = self._resolve(name)
            if p:
                loaded.append(name)
            else:
                missing.append(name)
        rules_dir = self._resolve_dir("rules")
        if rules_dir:
            loaded.append(f"rules/ ({len(list(rules_dir.glob('*.md')))} files)")
        else:
            missing.append("rules/")
        return {"loaded": loaded, "missing": missing, "has_prompt": self._cached_prompt is not None}

    def get_persona_name(self) -> str:
        core = self._read("core.md")
        for line in core.split("\n"):
            line = line.strip()
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
            if line.startswith("## ") and "Luna" in line:
                return "Luna"
            if "Luna" in line:
                return "Luna"
        return "Luna"
