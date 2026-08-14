"""
Multi-turn delegation support for maintaining context across delegations.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import uuid

from core.observability import get_tracer, get_metrics, MetricNames, trace_span
from core.agent_core import AgentCore
from session.manager import SessionManager


@dataclass
class DelegationSession:
    """Represents a multi-turn delegation session."""
    delegation_id: str
    target: str  # "luna", "aqua"
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    turns: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    agent_session_id: str | None = None  # Luna's session ID
    max_turns: int = 10
    ttl_seconds: int = 3600  # 1 hour default TTL
    
    def is_expired(self) -> bool:
        return datetime.utcnow() - self.last_activity > timedelta(seconds=self.ttl_seconds)
    
    def add_turn(self, user_message: str, assistant_response: str, metadata: dict | None = None):
        """Add a turn to the delegation session."""
        self.turns.append({
            "turn": len(self.turns) + 1,
            "timestamp": datetime.utcnow().isoformat(),
            "user": user_message,
            "assistant": assistant_response,
            "metadata": metadata or {},
        })
        self.last_activity = datetime.utcnow()
        
        # Trim if exceeding max turns
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]


class DelegationManager:
    """
    Manages multi-turn delegation sessions across Luna and Aqua.
    
    Allows Emma to continue conversations with subordinate AIs across
    multiple delegation requests, maintaining context and session state.
    """
    
    def __init__(
        self,
        luna_agent: Any | None = None,
        aqua_agent: Any | None = None,
        session_managers: dict[str, Any] | None = None,
    ):
        self._delegations: dict[str, DelegationSession] = {}
        self._lock = __import__("threading").RLock()
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # Agent references for session management
        self._luna_agent = luna_agent
        self._aqua_agent = aqua_agent
        self._session_managers = session_managers or {}
    
    async def start(self):
        """Start the delegation manager."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """Stop the delegation manager."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    def create_delegation(
        self,
        target: str,
        initial_context: dict | None = None,
        delegation_id: str | None = None,
    ) -> DelegationSession:
        """Create a new multi-turn delegation session."""
        delegation_id = delegation_id or str(uuid.uuid4())[:16]
        
        with self._lock:
            session = DelegationSession(
                delegation_id=delegation_id,
                target=target,
                context=initial_context or {},
            )
            self._delegations[delegation_id] = session
            return session
    
    def get_delegation(self, delegation_id: str) -> Optional[DelegationSession]:
        """Get a delegation session by ID."""
        with self._lock:
            session = self._delegations.get(delegation_id)
            if session and session.is_expired():
                del self._delegations[delegation_id]
                return None
            return session
    
    def add_turn(
        self,
        delegation_id: str,
        user_message: str,
        assistant_response: str,
        metadata: dict | None = None,
    ) -> bool:
        """Add a turn to an existing delegation."""
        with self._lock:
            session = self._delegations.get(delegation_id)
            if not session:
                return False
            session.add_turn(user_message, assistant_response, metadata)
            return True
    
    def update_context(self, delegation_id: str, context: dict) -> bool:
        """Update the context of a delegation session."""
        with self._lock:
            session = self._delegations.get(delegation_id)
            if not session:
                return False
            session.context.update(context)
            session.last_activity = datetime.utcnow()
            return True
    
    def get_or_create_agent_session(self, delegation_id: str) -> str | None:
        """Get or create an agent session for the delegation."""
        with self._lock:
            session = self._delegations.get(delegation_id)
            if not session:
                return None
            
            # This would integrate with the target agent's session manager
            # For now, return the stored agent session ID
            return session.agent_session_id
    
    def set_agent_session(self, delegation_id: str, agent_session_id: str):
        """Set the agent session ID for a delegation."""
        with self._lock:
            session = self._delegations.get(delegation_id)
            if session:
                session.agent_session_id = agent_session_id
    
    def list_active_delegations(self) -> list[dict]:
        """List all active delegation sessions."""
        with self._lock:
            return [
                {
                    "delegation_id": s.delegation_id,
                    "target": s.target,
                    "created_at": s.created_at.isoformat(),
                    "last_activity": s.last_activity.isoformat(),
                    "turns": len(s.turns),
                    "turns_history": s.turns,
                    "context": s.context,
                }
                for s in self._delegations.values()
                if not s.is_expired()
            ]
    
    def delete_delegation(self, delegation_id: str) -> bool:
        """Delete a delegation session."""
        with self._lock:
            if delegation_id in self._delegations:
                del self._delegations[delegation_id]
                return True
            return False
    
    async def _cleanup_loop(self):
        """Periodically clean up expired delegations."""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
    
    def _cleanup_expired(self):
        """Remove expired delegations."""
        with self._lock:
            expired = [
                d_id for d_id, session in self._delegations.items()
                if session.is_expired()
            ]
            for d_id in expired:
                del self._delegations[d_id]


# Global delegation manager instance
_delegation_manager: Optional[Any] = None


def get_delegation_manager(
    luna_agent: Any | None = None,
    aqua_agent: Any | None = None,
    session_managers: dict[str, Any] | None = None,
) -> DelegationManager:
    """Get or create the global delegation manager."""
    global _delegation_manager
    if _delegation_manager is None:
        _delegation_manager = DelegationManager(luna_agent, aqua_agent, session_managers)
    if luna_agent:
        _delegation_manager._luna_agent = luna_agent
    if aqua_agent:
        _delegation_manager._aqua_agent = aqua_agent
    if session_managers:
        _delegation_manager._session_managers = session_managers
    return _delegation_manager


async def delegate_turn(
    manager: DelegationManager,
    delegation_id: str | None,
    target: str,
    user_message: str,
    agent: Any,  # Luna or Aqua agent
    session_manager: Any | None = None,
    context: dict | None = None,
    stream: bool = False,
) -> tuple[str, str | None]:
    """
    Execute a single turn in a multi-turn delegation.
    
    Returns:
        Tuple of (assistant_response, delegation_id)
    """
    from core.observability import get_metrics, MetricNames, trace_span, get_tracer
    
    metrics = get_metrics()
    tracer = get_tracer("emma")
    
    # Get or create delegation
    with tracer.span("emma", "delegation_turn", tags={"target": target}):
        if delegation_id:
            session = manager.get_delegation(delegation_id)
            if not session:
                # Delegation expired or not found, create new
                session = manager.create_delegation(target, context)
                delegation_id = session.delegation_id
        else:
            session = manager.create_delegation(target, context)
            delegation_id = session.delegation_id
        
        # Build context including history
        context = session.context.copy()
        if session.turns:
            context["delegation_history"] = session.turns[-5:]  # Last 5 turns
        
        # Execute delegation
        # This would call the agent (Luna/Aqua) with the message
        # For now, return a placeholder
        response = f"[Delegation to {target}] {user_message}"
        
        # Record the turn
        manager.add_turn(delegation_id, user_message, response, {
            "target": target,
            "turn": len(session.turns),
        })
        
        return response, delegation_id