from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from .context import count_messages_tokens


COMPACT_THRESHOLD = 80000
TARGET_AFTER_COMPACT = 40000


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
                msgs = data.get("messages", [])
                last_content = ""
                for m in reversed(msgs):
                    if m["role"] == "user":
                        last_content = m.get("content", "")[:50]
                        break
                sessions.append({
                    "id": f.stem,
                    "created": data.get("created", ""),
                    "updated": data.get("updated", ""),
                    "message_count": len(msgs),
                    "preview": last_content,
                })
            except Exception:
                continue
        return sessions

    def save(self, messages: list[dict]) -> str:
        now = datetime.now(timezone.utc).isoformat()
        if self._current is None:
            self._current = now.replace(":", "-")
        path = self.session_dir / f"{self._current}.json"

        created = now
        if path.exists():
            try:
                existing = json.loads(path.read_text())
                created = existing.get("created", now)
            except Exception:
                pass

        total_tokens = count_messages_tokens(messages)
        if total_tokens > COMPACT_THRESHOLD:
            messages = self._compact(messages, TARGET_AFTER_COMPACT)

        data = {
            "id": self._current,
            "created": created,
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

    def delete(self, session_id: str) -> bool:
        path = self.session_dir / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            if self._current == session_id:
                self._current = None
            return True
        except Exception:
            return False

    def _compact(self, messages: list[dict], target_tokens: int) -> list[dict]:
        current_tokens = count_messages_tokens(messages)
        if current_tokens <= target_tokens:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        others = [m for m in messages if m["role"] != "system"]

        compacted = list(system_msgs)
        keep_pairs = 8

        pairs: list[list[dict]] = []
        current_pair: list[dict] = []
        for m in others:
            current_pair.append(m)
            if m["role"] == "user" and len(current_pair) > 1:
                pairs.append(current_pair[:-1])
                current_pair = [m]
            elif m["role"] == "tool":
                continue
        if current_pair:
            pairs.append(current_pair)
        if current_pair:
            pairs.append(current_pair)

        if len(pairs) <= keep_pairs:
            compacted.extend(others)
            return compacted

        pairs_to_keep = pairs[-keep_pairs:]
        pairs_to_summarize = pairs[:-keep_pairs]

        summary_lines = []
        for pair in pairs_to_summarize:
            user_msg = next((m for m in pair if m["role"] == "user"), None)
            assist_msg = next((m for m in pair if m["role"] == "assistant"), None)
            user_text = (user_msg.get("content", "") or "")[:80].strip() if user_msg else ""
            assist_text = (assist_msg.get("content", "") or "")[:80].strip() if assist_msg else ""
            parts = []
            if user_text:
                parts.append(f"user: {user_text}")
            if assist_text:
                parts.append(f"→ {assist_text}")
            if parts:
                summary_lines.append("; ".join(parts))

        if summary_lines:
            compacted.append({
                "role": "system",
                "content": "[Compacted earlier conversation]\n" + "\n".join(summary_lines[-15:]),
            })

        for pair in pairs_to_keep:
            compacted.extend(pair)

        return compacted
