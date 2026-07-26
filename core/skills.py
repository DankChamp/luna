from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    instructions: str = ""
    path: Path | None = None


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    meta: dict[str, str] = {}
    key = ""
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            meta[key] = val
        elif key:
            meta[key] += " " + line.strip()
    return meta, body


def _parse_triggers(raw: str) -> list[str]:
    if raw.startswith("["):
        raw = raw.strip("[]")
        return [t.strip().strip("'\"") for t in raw.split(",")]
    return [raw.strip()]


class SkillManager:
    def __init__(self, *search_dirs: str | Path):
        self.search_dirs = [Path(d).expanduser() for d in search_dirs]
        self._skills: dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        for d in self.search_dirs:
            for f in sorted(d.rglob("SKILL.md")):
                self._load_file(f)

    def _load_file(self, path: Path):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", path.parent.name)
        desc = meta.get("description", "")
        triggers = _parse_triggers(meta.get("triggers", "[]"))
        skill = Skill(
            name=name,
            description=desc,
            triggers=triggers,
            instructions=body.strip(),
            path=path,
        )
        self._skills[name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def match(self, query: str) -> list[Skill]:
        q = query.lower()
        matched = []
        for skill in self._skills.values():
            if any(t.lower() in q for t in skill.triggers):
                matched.append(skill)
                continue
            if any(w in skill.description.lower() for w in q.split()):
                matched.append(skill)
        return matched