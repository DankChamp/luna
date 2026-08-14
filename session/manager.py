from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .context import count_messages_tokens


COMPACT_THRESHOLD = 80000
TARGET_AFTER_COMPACT = 40000


class SessionManager:
    def __init__(self, session_dir: str):
        self.session_dir = Path(session_dir).expanduser()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current: str | None = None
        self._save_debounce: Optional[float] = None
        self._dirty = False

    @property
    def current(self) -> str | None:
        return self._current

    @staticmethod
    def _capture_metadata() -> dict:
        meta = {
            "path": os.getcwd(),
            "name": Path.cwd().name,
            "repo": "",
            "branch": "",
            "commit": "",
            "files": [],
            "summary": "",
        }
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                meta["repo"] = r.stdout.strip()
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                meta["branch"] = r.stdout.strip()
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                meta["commit"] = r.stdout.strip()[:12]
        except Exception:
            pass
        return meta

    @staticmethod
    def _auto_summarize(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m["role"] == "assistant":
                content = m.get("content", "") or ""
                return content[:120].strip()
        return ""

    def list_sessions(self) -> list[dict]:
        sessions = []
        for f in sorted(self.session_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                msgs = data.get("messages", [])
                project = data.get("project", {})
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
                    "project": project,
                })
            except Exception:
                continue
        return sessions

    def save(self, messages: list[dict], debounce: bool = True) -> str:
        """Save session to disk. If debounce=True, batches saves to avoid thrashing."""
        import time
        now = datetime.now(timezone.utc).isoformat()
        if self._current is None:
            self._current = now.replace(":", "-")
        path = self.session_dir / f"{self._current}.json"

        created = now
        existing_project = None
        if path.exists():
            try:
                existing = json.loads(path.read_text())
                created = existing.get("created", now)
                existing_project = existing.get("project")
            except Exception:
                pass

        total_tokens = count_messages_tokens(messages)
        if total_tokens > COMPACT_THRESHOLD:
            messages = self._compact(messages, TARGET_AFTER_COMPACT)

        project = self._capture_metadata()
        if existing_project:
            project["path"] = existing_project.get("path", project["path"])
            project["name"] = existing_project.get("name", project["name"])
            project["summary"] = existing_project.get("summary", "")

        summary = self._auto_summarize(messages)
        if summary:
            project["summary"] = summary

        data = {
            "id": self._current,
            "created": created,
            "updated": now,
            "project": project,
            "messages": messages,
        }
        
        # Atomic write
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, indent=2))
        temp_path.replace(path)
        
        self._dirty = False
        return self._current

    def mark_dirty(self):
        """Mark session as needing save (for debounced saving)."""
        self._dirty = True

    def flush(self, messages: list[dict]) -> str:
        """Force save if dirty."""
        if self._dirty:
            return self.save(messages, debounce=False)
        return self._current or ""

    def load(self, session_id: str) -> dict | None:
        path = self.session_dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            self._current = session_id
            return data
        except Exception:
            return None

    def new(self):
        self._current = None
        self._dirty = False

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
        """Compact conversation history while preserving tool call/result pairs."""
        current_tokens = count_messages_tokens(messages)
        if current_tokens <= target_tokens:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        others = [m for m in messages if m["role"] != "system"]

        compacted = list(system_msgs)
        keep_pairs = 8

        # Group messages into conversation turns (user -> assistant + tool calls/results)
        turns: list[list[dict]] = []
        current_turn: list[dict] = []
        
        for m in others:
            current_turn.append(m)
            # End of turn: assistant message followed by user message or end of list
            # Tool calls/results are part of the assistant's turn
            if m["role"] == "assistant":
                # Look ahead to see if next is user (turn boundary)
                # But don't break on tool messages - they belong to this turn
                continue
            elif m["role"] == "user" and len(current_turn) >= 2 and current_turn[-2].get("role") == "assistant":
                # Previous was assistant, this is new user message - turn boundary
                turns.append(current_turn[:-1])
                current_turn = [m]
        
        if current_turn:
            turns.append(current_turn)

        if len(turns) <= keep_pairs:
            compacted.extend(others)
            return compacted

        turns_to_keep = turns[-keep_pairs:]
        turns_to_summarize = turns[:-keep_pairs]

        summary_lines = []
        for turn in turns_to_summarize:
            user_msg = next((m for m in turn if m["role"] == "user"), None)
            assist_msg = next((m for m in turn if m["role"] == "assistant"), None)
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

        for turn in turns_to_keep:
            compacted.extend(turn)

        return compacted
