from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from dataclasses import dataclass
import aiosqlite
from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    Integer,
    ForeignKey,
    Index,
    select,
    func,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from core.config_manager import ConfigManager
from core.errors import SessionError, ConfigMigrationError


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    project_path = Column(String(512))
    project_name = Column(String(256))
    repo = Column(String(512))
    branch = Column(String(256))
    commit = Column(String(64))
    summary = Column(Text)
    message_count = Column(Integer, default=0)

    messages = relationship("MessageModel", back_populates="session", cascade="all, delete-orphan")
    tool_calls = relationship("ToolCallModel", back_populates="session", cascade="all, delete-orphan")


class MessageModel(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id"), nullable=False)
    role = Column(String(32), nullable=False)
    content = Column(Text)
    tool_calls_json = Column(Text)
    tool_call_id = Column(String(64))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("SessionModel", back_populates="messages")


class ToolCallModel(Base):
    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id"), nullable=False)
    tool_name = Column(String(128), nullable=False)
    arguments_json = Column(Text)
    result = Column(Text)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)

    session = relationship("SessionModel", back_populates="tool_calls")


@dataclass
class SessionData:
    id: str
    created_at: datetime
    updated_at: datetime
    project_path: str | None
    project_name: str | None
    repo: str | None
    branch: str | None
    commit: str | None
    summary: str | None
    message_count: int


class SessionDatabase:
    """SQLite-backed session storage with auto-migration from JSON."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or Path("~/.luna/sessions/sessions.db").expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = None
        self.session_factory = None

    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}",
            echo=False,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        await self._maybe_migrate_json_sessions()

    async def _maybe_migrate_json_sessions(self) -> None:
        """Migrate old JSON session files to SQLite."""
        json_dir = self.db_path.parent
        if not json_dir.exists():
            return

        json_files = list(json_dir.glob("*.json"))
        if not json_files:
            return

        # Skip sessions that were already migrated
        pending = []
        async with self.session_factory() as session:
            for json_file in json_files:
                data = json.loads(json_file.read_text())
                session_id = data.get("id", json_file.stem)
                existing = await session.get(SessionModel, session_id)
                if not existing:
                    pending.append(json_file)

        if not pending:
            return

        print(f"Migrating {len(pending)} JSON sessions to SQLite...")

        async with self.session_factory() as session:
            for json_file in pending:
                try:
                    await self._migrate_json_session(session, json_file)
                except Exception as e:
                    print(f"Failed to migrate {json_file}: {e}")

            await session.commit()

        print("Migration complete")

    async def _migrate_json_session(self, db_session: AsyncSession, json_file: Path) -> None:
        """Migrate a single JSON session file."""
        data = json.loads(json_file.read_text())

        session_id = data.get("id", json_file.stem)

        # Skip if this session was already migrated (idempotent)
        existing = await db_session.get(SessionModel, session_id)
        if existing:
            return

        def _parse_dt(value, default=None):
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except ValueError:
                    pass
            elif isinstance(value, (int, float)):
                return datetime.fromtimestamp(value, tz=timezone.utc)
            return default or datetime.now(timezone.utc)

        created_at = _parse_dt(data.get("created"))
        updated_at = _parse_dt(data.get("updated"), created_at)

        project = data.get("project", {})
        project_path = project.get("path")
        project_name = project.get("name")
        repo = project.get("repo")
        branch = project.get("branch")
        commit = project.get("commit")
        summary = project.get("summary", "")

        messages = data.get("messages", [])

        # Create session record
        session_model = SessionModel(
            id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            project_path=project_path,
            project_name=project_name,
            repo=repo,
            branch=branch,
            commit=commit,
            summary=summary,
            message_count=len(messages),
        )
        db_session.add(session_model)
        await db_session.flush()

        # Create messages
        for i, msg in enumerate(messages):
            msg_model = MessageModel(
                session_id=session_id,
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                tool_calls_json=json.dumps(msg.get("tool_calls")) if msg.get("tool_calls") else None,
                tool_call_id=msg.get("tool_call_id"),
            )
            db_session.add(msg_model)

    async def create_session(
        self,
        session_id: str,
        project_path: str | None = None,
        project_name: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
        commit: str | None = None,
        summary: str | None = None,
    ) -> SessionData:
        """Create a new session."""
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            session_model = SessionModel(
                id=session_id,
                created_at=now,
                updated_at=now,
                project_path=project_path,
                project_name=project_name,
                repo=repo,
                branch=branch,
                commit=commit,
                summary=summary,
                message_count=0,
            )
            session.add(session_model)
            await session.commit()

            return SessionData(
                id=session_id,
                created_at=now,
                updated_at=now,
                project_path=project_path,
                project_name=project_name,
                repo=repo,
                branch=branch,
                commit=commit,
                summary=summary,
                message_count=0,
            )

    async def get_session(self, session_id: str) -> SessionData | None:
        """Get session by ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            model = result.scalar_one_or_none()
            if not model:
                return None

            return SessionData(
                id=model.id,
                created_at=model.created_at,
                updated_at=model.updated_at,
                project_path=model.project_path,
                project_name=model.project_name,
                repo=model.repo,
                branch=model.branch,
                commit=model.commit,
                summary=model.summary,
                message_count=model.message_count,
            )

    async def list_sessions(self, limit: int = 50) -> list[SessionData]:
        """List recent sessions."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SessionModel)
                .order_by(SessionModel.updated_at.desc())
                .limit(limit)
            )
            models = result.scalars().all()

            return [
                SessionData(
                    id=m.id,
                    created_at=m.created_at,
                    updated_at=m.updated_at,
                    project_path=m.project_path,
                    project_name=m.project_name,
                    repo=m.repo,
                    branch=m.branch,
                    commit=m.commit,
                    summary=m.summary,
                    message_count=m.message_count,
                )
                for m in models
            ]

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        """Add a message to a session."""
        async with self.session_factory() as session:
            msg = MessageModel(
                session_id=session_id,
                role=role,
                content=content,
                tool_calls_json=json.dumps(tool_calls) if tool_calls else None,
                tool_call_id=tool_call_id,
            )
            session.add(msg)

            # Update session message count and timestamp
            await session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            result = await session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            model = result.scalar_one_or_none()
            if model:
                model.message_count += 1
                model.updated_at = datetime.now(timezone.utc)

            await session.commit()

    async def add_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict,
        result: str | None = None,
        completed: bool = False,
    ) -> int:
        """Add a tool call record."""
        async with self.session_factory() as session:
            tool_call = ToolCallModel(
                session_id=session_id,
                tool_name=tool_name,
                arguments_json=json.dumps(arguments),
                result=result,
                completed_at=datetime.now(timezone.utc) if completed else None,
            )
            session.add(tool_call)
            await session.commit()
            return tool_call.id

    async def update_tool_call(self, call_id: int, result: str) -> None:
        """Update tool call with result."""
        async with self.session_factory() as session:
            await session.execute(
                ToolCallModel.__table__.update()
                .where(ToolCallModel.id == call_id)
                .values(result=result, completed_at=datetime.now(timezone.utc))
            )
            await session.commit()

    async def get_messages(self, session_id: str) -> list[dict]:
        """Get all messages for a session."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.id)
            )
            models = result.scalars().all()

            messages = []
            for m in models:
                msg = {
                    "role": m.role,
                    "content": m.content,
                }
                if m.tool_calls_json:
                    msg["tool_calls"] = json.loads(m.tool_calls_json)
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                messages.append(msg)
            return messages

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its data."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            model = result.scalar_one_or_none()
            if not model:
                return False

            await session.delete(model)
            await session.commit()
            return True

    async def update_session(
        self,
        session_id: str,
        summary: str | None = None,
        branch: str | None = None,
    ) -> bool:
        """Update session metadata."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            model = result.scalar_one_or_none()
            if not model:
                return False

            if summary is not None:
                model.summary = summary
            if branch is not None:
                model.branch = branch
            model.updated_at = datetime.now(timezone.utc)

            await session.commit()
            return True

    async def close(self) -> None:
        """Close database connections."""
        if self.engine:
            await self.engine.dispose()


async def get_session_database(db_path: Path | None = None) -> SessionDatabase:
    """Get or create session database instance."""
    db = SessionDatabase(db_path)
    await db.initialize()
    return db