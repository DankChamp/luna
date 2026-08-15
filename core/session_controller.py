from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass

from session.manager import SessionManager
from core.agent_core import AgentCore


@dataclass
class SessionControllerConfig:
    """Configuration for SessionController."""
    session_dir: str = "~/.luna/sessions"
    auto_save_interval: float = 5.0  # seconds
    auto_compact_threshold: int = 80000


class SessionController:
    """
    Manages session lifecycle: load, save, switch, create, delete.
    
    Handles debounced saving, auto-compaction, and session switching.
    """
    
    def __init__(
        self,
        agent_core: AgentCore,
        config: SessionControllerConfig | None = None,
        on_session_change: Callable[[str | None], Awaitable[None]] | None = None,
    ):
        self.agent_core = agent_core
        self.config = config or SessionControllerConfig()
        self.on_session_change = on_session_change
        
        self.session_mgr = SessionManager(self.config.session_dir)
        self._current_session_id: str | None = None
        self._save_task: asyncio.Task | None = None
        self._dirty = False
        self._shutdown = False
    
    async def start(self):
        """Start the session controller (auto-save loop)."""
        self._shutdown = False
        self._save_task = asyncio.create_task(self._auto_save_loop())
    
    async def stop(self):
        """Stop the session controller and flush pending saves."""
        self._shutdown = True
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        # Final flush
        await self.flush()
    
    def _mark_dirty(self):
        """Mark session as needing save."""
        self._dirty = True
    
    async def flush(self):
        """Force save if dirty."""
        if self._dirty:
            await self._save_current()
    
    async def _auto_save_loop(self):
        """Background loop for debounced session saving."""
        while not self._shutdown:
            await asyncio.sleep(self.config.auto_save_interval)
            if self._dirty:
                await self.flush()
    
    async def _save_current(self) -> str | None:
        """Save current session."""
        if not self._dirty:
            return None

        session_id = await self.session_mgr.save(
            self.agent_core.messages,
            debounce=False  # We're explicitly flushing
        )
        self._current_session_id = session_id
        self._dirty = False
        return session_id
    
    async def load_session(self, session_id: str) -> bool:
        """Load a session by ID."""
        data = await self.session_mgr.load(session_id)
        if not data:
            return False
        
        # Save current session before switching (if dirty)
        if self._dirty and self._current_session_id:
            await self._save_current()
        
        # Load new session
        self.agent_core.load_messages(data.get("messages", []))
        self._current_session_id = session_id
        self._dirty = False
        
        # Notify listeners
        if self.on_session_change:
            await self.on_session_change(session_id)
        
        return True
    
    async def new_session(self) -> str:
        """Create a new session."""
        # Save current if dirty
        if self._dirty and self._current_session_id:
            await self._save_current()
        
        # Create new
        self.agent_core.reset()
        self.session_mgr.new()
        self._current_session_id = None
        self._dirty = True  # Will get ID on first save
        
        if self.on_session_change:
            await self.on_session_change(None)
        
        return self._current_session_id or "new"
    
    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        result = await self.session_mgr.delete(session_id)
        if result and self._current_session_id == session_id:
            self._current_session_id = None
            self.agent_core.reset()
            self._dirty = False
            if self.on_session_change:
                await self.on_session_change(None)
        return result
    
    async def list_sessions(self) -> list[dict]:
        """List all sessions."""
        return await self.session_mgr.list_sessions()
    
    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id
    
    def mark_dirty(self):
        """Public method to mark session dirty."""
        self._dirty = True