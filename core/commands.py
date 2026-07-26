from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
ARG_RE = re.compile(r"\$(\d+|\w+)")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    meta: dict = {}
    key = ""
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            meta[key] = val
        elif key:
            meta[key] += " " + line.strip()
    return meta, body.strip()


def _expand_template(template: str, args: list[str]) -> str:
    expanded = template.replace("$ARGUMENTS", " ".join(args))
    for i, arg in enumerate(args, start=1):
        expanded = expanded.replace(f"${i}", arg)
    remaining = ARG_RE.findall(expanded)
    for ref in remaining:
        expanded = expanded.replace(f"${ref}", "")
    return expanded.strip()


@dataclass
class CustomCommand:
    name: str
    description: str
    template: str
    agent: str | None = None
    model: str | None = None
    subtask: bool = False

    def expand(self, args: list[str]) -> str:
        return _expand_template(self.template, args)


class CommandLoader:
    def __init__(self, *search_dirs: str | Path):
        self.search_dirs = [Path(d).expanduser().resolve() for d in search_dirs]
        self._commands: dict[str, CustomCommand] = {}
        self._load_all()

    def _load_all(self):
        for d in self.search_dirs:
            if not d.exists():
                continue
            for f in sorted(d.iterdir()):
                if f.suffix == ".md" and f.is_file():
                    self._load_file(f)

    def _load_file(self, path: Path):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", path.stem)
        low = name.lower()
        self._commands[low] = CustomCommand(
            name=low,
            description=meta.get("description", ""),
            template=body,
            agent=meta.get("agent"),
            model=meta.get("model"),
            subtask=meta.get("subtask", "").lower() == "true",
        )

    def get(self, name: str) -> CustomCommand | None:
        return self._commands.get(name.lower())

    def list_commands(self) -> list[CustomCommand]:
        seen: set[str] = set()
        result = []
        for c in self._commands.values():
            if c.name not in seen:
                seen.add(c.name)
                result.append(c)
        return result
