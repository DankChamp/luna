from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path


class SessionManager:
    def __init__(self, session_dir: str):
        self.session_dir = Path(session_dir).expanduser()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current: str | None = None

    @property
    def current(self) -> str | None:
        return self._current

    def list_sessions(self) -> list[dict]:
        sessions = []
        for f in sorted(self.session_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                sessions.append({
                    "id": f.stem,
                    "created": data.get("created", ""),
                    "updated": data.get("updated", ""),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                continue
        return sessions

    def save(self, messages: list[dict]) -> str:
        now = datetime.now().isoformat()
        if self._current is None:
            self._current = now.replace(":", "-")
        path = self.session_dir / f"{self._current}.json"
        data = {
            "id": self._current,
            "created": path.stat().st_ctime if path.exists() else now,
            "updated": now,
            "messages": messages,
        }
        path.write_text(json.dumps(data, indent=2))
        return self._current

    def load(self, session_id: str) -> list[dict] | None:
        path = self.session_dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            self._current = session_id
            return data.get("messages", [])
        except Exception:
            return None

    def new(self):
        self._current = None
