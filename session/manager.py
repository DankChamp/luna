from __future__ import annotations
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .context import count_messages_tokens
from core.session_db import SessionDatabase, get_session_database
from core.errors import SessionError


COMPACT_THRESHOLD = 80000
TARGET_AFTER_COMPACT = 40000


class SessionManager:
    """Session manager with SQLite backend and JSON auto-migration."""

    def __init__(self, session_dir: str, db_path: Path | None = None):
        self.session_dir = Path(session_dir).expanduser()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current: str | None = None
        self._save_debounce: Optional[float] = None
        self._dirty = False
        self._db: SessionDatabase | None = None
        self._db_path = db_path

    async def _get_db(self) -> SessionDatabase:
        """Get or create database instance."""
        if self._db is None:
            self._db = await get_session_database(self._db_path)
        return self._db

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

    async def list_sessions(self) -> list[dict]:
        db = await self._get_db()
        sessions = await db.list_sessions(limit=50)
        result = []
        for s in sessions:
            result.append({
                "id": s.id,
                "created": s.created_at.isoformat(),
                "updated": s.updated_at.isoformat(),
                "message_count": s.message_count,
                "preview": s.summary or "",
                "project": {
                    "path": s.project_path,
                    "name": s.project_name,
                    "repo": s.repo,
                    "branch": s.branch,
                    "commit": s.commit,
                    "summary": s.summary,
                },
            })
        return result

    async def save(self, messages: list[dict], debounce: bool = True) -> str:
        """Save session to database. If debounce=True, batches saves to avoid thrashing."""
        now = datetime.now(timezone.utc)
        db = await self._get_db()

        if self._current is None:
            session_id = now.strftime("%Y%m%d-%H%M%S-%f")
            self._current = session_id
        else:
            session_id = self._current

        existing_session = await db.get_session(session_id)
        created = existing_session.created_at if existing_session else datetime.now(timezone.utc)

        total_tokens = count_messages_tokens(messages)
        if total_tokens > COMPACT_THRESHOLD:
            messages = self._compact(messages, TARGET_AFTER_COMPACT)

        project = self._capture_metadata()
        summary = self._auto_summarize(messages)

        await db.create_session(
            session_id=session_id,
            project_path=project.get("path"),
            project_name=project.get("name"),
            repo=project.get("repo"),
            branch=project.get("branch"),
            commit=project.get("commit"),
            summary=summary,
        )

        # Save messages
        for msg in messages:
            await db.add_message(
                session_id=session_id,
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )

        self._dirty = False
        return session_id

    def mark_dirty(self):
        """Mark session as needing save (for debounced saving)."""
        self._dirty = True

    async def flush(self, messages: list[dict]) -> str:
        """Force save if dirty."""
        if self._dirty:
            return await self.save(messages, debounce=False)
        return self._current or ""

    async def load(self, session_id: str) -> dict | None:
        db = await self._get_db()
        messages = await db.get_messages(session_id)
        if not messages:
            return None

        session_data = await db.get_session(session_id)
        if not session_data:
            return None

        self._current = session_id
        return {
            "id": session_data.id,
            "created": session_data.created_at.isoformat(),
            "updated": session_data.updated_at.isoformat(),
            "project": {
                "path": session_data.project_path,
                "name": session_data.project_name,
                "repo": session_data.repo,
                "branch": session_data.branch,
                "commit": session_data.commit,
                "summary": session_data.summary,
            },
            "messages": messages,
        }

    def new(self):
        self._current = None
        self._dirty = False

    async def delete(self, session_id: str) -> bool:
        db = await self._get_db()
        result = await db.delete_session(session_id)
        if result and self._current == session_id:
            self._current = None
        return result

    def _compact(self, messages: list[dict], target_tokens: int) -> list[dict]:
        """Compact conversation history while preserving tool call/result pairs."""
        current_tokens = count_messages_tokens(messages)
        if current_tokens <= target_tokens:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        others = [m for m in messages if m["role"] != "system"]

        compacted = list(system_msgs)
        keep_pairs = 8

        turns: list[list[dict]] = []
        current_turn: list[dict] = []

        for m in others:
            current_turn.append(m)
            if m["role"] == "assistant":
                continue
            elif m["role"] == "user" and len(current_turn) >= 2 and current_turn[-2].get("role") == "assistant":
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