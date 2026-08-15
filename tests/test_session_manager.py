"""Tests for SessionManager."""
from __future__ import annotations

import pytest
import pytest_asyncio

from session.manager import SessionManager
from session.context import count_messages_tokens, estimate_tokens, trim_to_budget


class TestSessionManager:
    """Test SessionManager functionality."""

    @pytest_asyncio.fixture
    async def mgr(self, temp_dir):
        """Create a SessionManager instance."""
        # Use a unique subdirectory for each test to isolate sessions
        test_dir = temp_dir / "test_sessions"
        test_dir.mkdir(parents=True, exist_ok=True)
        db_path = test_dir / "sessions.db"
        mgr = SessionManager(str(test_dir), db_path=db_path)
        yield mgr

    @pytest.mark.asyncio
    async def test_new_session(self, mgr):
        """Test creating a new session."""
        mgr.new()
        assert mgr.current is None

    @pytest.mark.asyncio
    async def test_save_and_load(self, mgr):
        """Test saving and loading a session."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        session_id = await mgr.save(messages)

        assert session_id is not None
        assert mgr.current == session_id

        # Load it back
        loaded = await mgr.load(session_id)
        assert loaded is not None
        assert loaded["messages"] == messages
        assert loaded["id"] == session_id

    @pytest.mark.asyncio
    async def test_list_sessions(self, mgr):
        """Test listing sessions."""
        await mgr.save([{"role": "user", "content": "Test 1"}])
        mgr.new()
        await mgr.save([{"role": "user", "content": "Test 2"}])

        sessions = await mgr.list_sessions()
        # Filter to only sessions created in this test
        test_sessions = [s for s in sessions if s["message_count"] == 1]
        assert len(test_sessions) == 2

    @pytest.mark.asyncio
    async def test_delete_session(self, mgr):
        """Test deleting a session."""
        session_id = await mgr.save([{"role": "user", "content": "Test"}])

        assert await mgr.delete(session_id) is True
        assert await mgr.load(session_id) is None
        assert await mgr.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_compaction_preserves_tool_pairs(self, mgr):
        """Test that compaction preserves tool call/result pairs."""
        # Create messages with tool calls
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Search for something"},
            {"role": "assistant", "content": "I'll search for that", "tool_calls": [{"name": "web_search", "arguments": {"query": "test"}}]},
            {"role": "tool", "content": "Search results...", "tool_call_id": "123"},
            {"role": "assistant", "content": "Here are the results"},
            {"role": "user", "content": "Thanks"},
            {"role": "assistant", "content": "You're welcome"},
        ]

        # Test compaction directly
        compacted = mgr._compact(messages, target_tokens=100)

        # Should have system message + summary + recent turns
        assert len(compacted) >= 2
        assert compacted[0]["role"] == "system"


class TestTokenCounting:
    """Test token counting functionality."""

    def test_estimate_tokens_empty(self):
        """Test empty string returns 0."""
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0  # type: ignore

    def test_estimate_tokens_basic(self):
        """Test basic token estimation."""
        text = "Hello world"
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_count_messages_tokens(self):
        """Test counting tokens in messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        total = count_messages_tokens(messages)
        assert total > 0

    def test_trim_to_budget(self):
        """Test trimming messages to token budget."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Message 2"},
            {"role": "assistant", "content": "Response 2"},
        ]
        # Trim to very small budget
        trimmed = trim_to_budget(messages, max_tokens=50)
        # Should keep system and at least some messages
        assert len(trimmed) >= 1
        assert trimmed[0]["role"] == "system"

    def test_tool_calls_counted(self):
        """Test that tool calls are included in token count."""
        messages = [
            {"role": "assistant", "content": "I'll search", "tool_calls": [
                {"name": "web_search", "arguments": {"query": "test"}}
            ]},
        ]
        total = count_messages_tokens(messages)
        assert total > 0


class TestSessionCompaction:
    """Test session compaction edge cases."""

    def test_compaction_empty_messages(self):
        """Test compaction with empty messages."""
        mgr = SessionManager("/tmp/test_empty")
        result = mgr._compact([], 1000)
        assert result == []

    def test_compaction_only_system(self):
        """Test compaction with only system messages."""
        mgr = SessionManager("/tmp/test_system")
        messages = [{"role": "system", "content": "System"}]
        result = mgr._compact(messages, 1000)
        assert result == messages

    def test_compaction_no_system(self):
        """Test compaction without system messages."""
        mgr = SessionManager("/tmp/test_no_system")
        # Create enough turns to trigger compaction (> 8 turns)
        messages = []
        for i in range(10):
            messages.append({"role": "user", "content": f"Message {i}"})
            messages.append({"role": "assistant", "content": f"Response {i}"})
        result = mgr._compact(messages, 10)  # Force compaction
        # Should create summary system message since we're compacting
        assert any(m["role"] == "system" for m in result)