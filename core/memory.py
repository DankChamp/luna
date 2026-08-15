from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.paths import data_home


MEMORY_DIR = data_home() / "memory"

FACT_PATTERNS = [
    re.compile(r"(?:my|the user's?|the)\s+(\w+(?:\s+\w+){0,3})\s+is\s+(.+?)[.!\n]", re.IGNORECASE),
    re.compile(r"(?:i|we)\s+(?:use|prefer|like)\s+(.+?)[.!\n]", re.IGNORECASE),
    re.compile(r"(?:don't|do not)\s+(?:use|want)\s+(.+?)[.!\n]", re.IGNORECASE),
]


class MemoryStore:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path or MEMORY_DIR / "memory.json")
        self._data: dict = {"facts": [], "preferences": {}, "project_state": {}}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def add_fact(self, fact: str, source: str = "conversation"):
        entry = {
            "fact": fact,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        existing = [f for f in self._data["facts"] if f["fact"] == fact]
        if not existing:
            self._data["facts"].append(entry)
            self._save()

    def set_preference(self, key: str, value: str):
        self._data["preferences"][key] = value
        self._save()

    def get_preference(self, key: str) -> Optional[str]:
        return self._data["preferences"].get(key)

    def set_project_state(self, key: str, value: str):
        self._data["project_state"][key] = value
        self._save()

    def get_project_state(self, key: str) -> Optional[str]:
        return self._data["project_state"].get(key)

    def extract_facts(self, text: str) -> list[str]:
        facts = []
        for pattern in FACT_PATTERNS:
            for match in pattern.finditer(text):
                fact = match.group(0).strip()
                if len(fact) > 10 and len(fact) < 200:
                    facts.append(fact)
        return facts

    def summarize(self) -> str:
        parts = []
        if self._data["facts"]:
            parts.append("## Memory")
            for f in self._data["facts"][-20:]:
                parts.append(f"- {f['fact']}")
        if self._data["preferences"]:
            parts.append("## Preferences")
            for k, v in self._data["preferences"].items():
                parts.append(f"- {k}: {v}")
        if self._data["project_state"]:
            parts.append("## Project State")
            for k, v in self._data["project_state"].items():
                parts.append(f"- {k}: {v}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return dict(self._data)

    def from_dict(self, data: dict):
        self._data = {
            "facts": data.get("facts", []),
            "preferences": data.get("preferences", {}),
            "project_state": data.get("project_state", {}),
        }

    def save_to_file(self, session_id: str):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (MEMORY_DIR / f"{session_id}.json").write_text(
            json.dumps(self._data, indent=2)
        )

    def load_from_file(self, session_id: str):
        path = MEMORY_DIR / f"{session_id}.json"
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {"facts": [], "preferences": {}, "project_state": {}}
        else:
            self._data = {"facts": [], "preferences": {}, "project_state": {}}

    def clear(self):
        self._data = {"facts": [], "preferences": {}, "project_state": {}}
        self._save()
