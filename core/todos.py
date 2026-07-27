from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
TODO_FILE = Path.home() / ".luna" / "todos.json"


class TodoStore:
    def __init__(self):
        self._data: list[dict] = []
        self._load()

    def _load(self):
        if TODO_FILE.exists():
            try:
                self._data = json.loads(TODO_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._data = []

    def _save(self):
        TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
        TODO_FILE.write_text(json.dumps(self._data, indent=2))

    def add(self, content: str) -> dict:
        item = {
            "id": str(len(self._data) + 1),
            "content": content,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._data.append(item)
        self._save()
        return item

    def done(self, item_id: str) -> bool:
        for item in self._data:
            if item["id"] == item_id:
                item["status"] = "done"
                self._save()
                return True
        return False

    def remove(self, item_id: str) -> bool:
        for i, item in enumerate(self._data):
            if item["id"] == item_id:
                self._data.pop(i)
                self._save()
                return True
        return False

    def list(self) -> list[dict]:
        return list(self._data)

    def count_pending(self) -> int:
        return sum(1 for t in self._data if t["status"] == "pending")

    def replace_all(self, items: list[dict]):
        self._data = items
        self._save()

    def clear(self):
        self._data = []
        self._save()

    def markdown_summary(self) -> str:
        if not self._data:
            return ""
        lines = ["## Todos"]
        for t in self._data:
            icon = "✓" if t["status"] == "done" else "○"
            lines.append(f"- {icon} {t['content']}")
        return "\n".join(lines)
